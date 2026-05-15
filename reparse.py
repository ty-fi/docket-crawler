"""
Re-parse all cached ZIPs into clean CSV + SQLite.
No network requests — reads from output/zips/ only.
"""

import csv
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path

from docx import Document

OUTPUT_DIR = Path("output")
ZIPS_DIR = OUTPUT_DIR / "zips"
EXTRACTED_DIR = OUTPUT_DIR / "extracted"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
DB_CSV = OUTPUT_DIR / "stf_pia_qa.csv"
DB_SQLITE = OUTPUT_DIR / "stf_pia_qa.sqlite"

FIELDNAMES = [
    "filing_api_id", "filing_description", "filing_date",
    "staff", "doc_id", "is_protective_disclosure", "zip_source",
    "question", "response",
    "attachment_filenames", "confidentiality_files",
]


STF_PATTERN = r'((?:PD\s+)?STF-[A-Z]{2,4}-\s*\d+\s*-\s*\d+)'


def extract_doc_id_from_text(paragraphs):
    """Scan first 15 non-blank paragraphs for STF-XXX-X-Y pattern."""
    non_blank = [p for p in paragraphs if p.strip()]
    for p in non_blank[:15]:
        m = re.search(STF_PATTERN, p, re.IGNORECASE)
        if m:
            # Normalize internal spaces (e.g. "STF- PIA-1-1" -> "STF-PIA-1-1")
            return re.sub(r'(?<=STF-)\s+|\s+(?=-)', '', m.group(1).upper())
    return None


def extract_doc_id_from_filename(filename):
    """Extract STF-XXX-X-Y from the file's basename as fallback."""
    basename = Path(filename).stem
    m = re.search(STF_PATTERN, basename, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return basename  # last resort: use full stem


def extract_staff(doc_id):
    """Extract the staff acronym from a doc_id like 'STF-PIA-1-1' -> 'PIA'."""
    m = re.search(r'STF-([A-Z]{2,4})-', doc_id, re.IGNORECASE)
    return m.group(1).upper() if m else ""


def parse_qa_docx(docx_bytes, zip_entry_name):
    """
    Parse a Q&A docx. Returns dict or None if no Q&A structure found.
    """
    try:
        doc = Document(io.BytesIO(docx_bytes))
    except Exception as e:
        print(f"    ERROR reading docx {zip_entry_name}: {e}")
        return None

    paragraphs = [p.text.strip() for p in doc.paragraphs]
    full_text = "\n".join(p for p in paragraphs if p)

    # Must have both Question and Response markers
    has_q = bool(re.search(r'(?i)question\s*:', full_text))
    has_r = bool(re.search(r'(?i)response\s*:', full_text))
    if not has_q or not has_r:
        return None

    # Try to find doc_id in text first, then fall back to filename
    doc_id = extract_doc_id_from_text(paragraphs)
    if not doc_id:
        doc_id = extract_doc_id_from_filename(zip_entry_name)

    # Is this a Protective Disclosure (PD) filing?
    basename = Path(zip_entry_name).name
    is_pd = bool(re.match(r'PD\s+STF-[A-Z]{2,4}', basename, re.IGNORECASE))

    # Split on standalone Question: / Response: markers
    q_match = re.search(r'(?im)^question\s*:?\s*$', full_text)
    r_match = re.search(r'(?im)^response\s*:?\s*$', full_text)

    if q_match and r_match and q_match.start() < r_match.start():
        question = full_text[q_match.end():r_match.start()].strip()
        response = full_text[r_match.end():].strip()
    else:
        # Inline markers: "Question: <text> Response: <text>"
        r_split = re.split(r'(?i)response\s*:', full_text, maxsplit=1)
        response = r_split[1].strip() if len(r_split) > 1 else ""
        q_split = re.split(r'(?i)question\s*:', r_split[0], maxsplit=1)
        question = q_split[1].strip() if len(q_split) > 1 else ""

    return {
        "doc_id": doc_id,
        "is_protective_disclosure": "yes" if is_pd else "no",
        "question": question,
        "response": response,
    }


def classify_zip_contents(zip_names):
    """
    Returns:
      qa_files        - individual Q&A response docx files
      conf_files      - confidentiality/trade secret assertion docx files
      other_files     - attachments and everything else
    """
    qa, conf, other = [], [], []
    for name in zip_names:
        basename = Path(name).name
        lower = basename.lower()

        if "drsum" in lower:
            continue
        if lower.endswith(".pdf") and "verification" in lower:
            continue
        if "trade secret" in lower or ("confidential" in lower and not re.search(r'STF-PIA-\d+-\d+', basename)):
            conf.append(name)
        elif (name.endswith(".docx")
              and re.search(r'STF-[A-Z]{2,4}-\d+-\d+', basename, re.IGNORECASE)
              and "attachment" not in lower):
            qa.append(name)
        else:
            other.append(name)
    return qa, conf, other


def main():
    # Load manifest to get filing metadata
    with open(MANIFEST_JSON) as f:
        manifest = json.load(f)

    # Build a lookup: zip_filename -> filing metadata
    zip_meta = {}
    for entry in manifest:
        if entry.get("status") == "ok":
            key = f"{entry['doc_id_api']}_{entry['zip_filename']}"
            zip_meta[key] = entry

    rows = []
    zip_files = sorted(ZIPS_DIR.glob("*.zip"))
    print(f"Re-parsing {len(zip_files)} cached ZIPs...\n")

    for zip_path in zip_files:
        # Derive filing_api_id from filename prefix (e.g. "216538_dkt_55378...")
        api_id = int(zip_path.name.split("_")[0])
        meta_key = zip_path.name  # key is the full zip filename without leading path
        # Find matching manifest entry
        filing_meta = next(
            (v for k, v in zip_meta.items() if zip_path.name.endswith(v["zip_filename"])),
            None
        )
        filing_description = filing_meta["description"] if filing_meta else ""
        filing_date = filing_meta.get("filed_date", "") if filing_meta else ""

        print(f"  {zip_path.name}")
        zf = zipfile.ZipFile(zip_path)
        all_names = zf.namelist()
        qa_files, conf_files, other_files = classify_zip_contents(all_names)
        print(f"    Q&A: {len(qa_files)}, conf: {len(conf_files)}, other: {len(other_files)}")

        for qa_name in qa_files:
            result = parse_qa_docx(zf.read(qa_name), qa_name)
            if not result:
                print(f"    WARNING: no Q&A structure in {Path(qa_name).name}")
                continue

            # Match attachments by doc_id prefix in filename
            doc_id_clean = re.sub(r'\s+', '', result["doc_id"])  # normalize spaces
            related_attachments = []
            for n in other_files:
                n_clean = re.sub(r'\s+', '', Path(n).name)
                if doc_id_clean in n_clean or result["doc_id"] in Path(n).name:
                    related_attachments.append(Path(n).name)

            rows.append({
                "filing_api_id": api_id,
                "filing_description": filing_description.strip().replace("\r", "").replace("\n", " ").replace("\t", " "),
                "filing_date": filing_date,
                "staff": extract_staff(result["doc_id"]),
                "doc_id": result["doc_id"],
                "is_protective_disclosure": result["is_protective_disclosure"],
                "zip_source": zip_path.name,
                "question": result["question"],
                "response": result["response"],
                "attachment_filenames": "; ".join(related_attachments),
                "confidentiality_files": "; ".join(Path(n).name for n in conf_files),
            })
            print(f"    + {result['doc_id']} {'[PD]' if result['is_protective_disclosure'] == 'yes' else ''}")

    print(f"\n{len(rows)} records total")

    # Write CSV
    with open(DB_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {DB_CSV}")

    # Write SQLite
    if DB_SQLITE.exists():
        DB_SQLITE.unlink()
    con = sqlite3.connect(DB_SQLITE)
    con.execute(f"""
        CREATE TABLE qa_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_api_id INTEGER,
            filing_description TEXT,
            filing_date TEXT,
            staff TEXT,
            doc_id TEXT,
            is_protective_disclosure TEXT,
            zip_source TEXT,
            question TEXT,
            response TEXT,
            attachment_filenames TEXT,
            confidentiality_files TEXT
        )
    """)
    con.execute("CREATE INDEX idx_doc_id ON qa_responses(doc_id)")
    con.execute("CREATE INDEX idx_filing_date ON qa_responses(filing_date)")
    con.execute("CREATE INDEX idx_staff ON qa_responses(staff)")
    con.execute("CREATE INDEX idx_is_pd ON qa_responses(is_protective_disclosure)")
    # Full-text search index for question + response content
    con.execute("""
        CREATE VIRTUAL TABLE qa_fts USING fts5(
            staff, doc_id, question, response,
            content='qa_responses', content_rowid='id'
        )
    """)
    con.executemany(
        f"INSERT INTO qa_responses ({','.join(FIELDNAMES)}) VALUES ({','.join('?' * len(FIELDNAMES))})",
        [[r[f] for f in FIELDNAMES] for r in rows],
    )
    # Populate FTS
    con.execute("INSERT INTO qa_fts(rowid, staff, doc_id, question, response) SELECT id, staff, doc_id, question, response FROM qa_responses")
    con.commit()
    con.close()
    print(f"Wrote {DB_SQLITE}")


if __name__ == "__main__":
    main()
