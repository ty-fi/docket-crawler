"""
Re-parse all cached ZIPs for a docket into clean CSV + SQLite.
No network requests — reads from output/docket_{id}/zips/ only.

Usage:
  python reparse.py                    # uses first docket in dockets.json
  python reparse.py --docket 55378
"""

import argparse
import csv
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path

from docx import Document

OUTPUT_DIR = Path("output")

FIELDNAMES = [
    "filing_api_id", "filing_description", "filing_date",
    "staff", "doc_id", "is_protective_disclosure", "zip_source",
    "question", "response",
    "attachment_filenames", "confidentiality_files",
]

STF_PATTERN = r'((?:PD\s+)?STF-[A-Z]{2,4}-\s*\d+\s*-\s*\d+)'


def load_docket_config(docket_id=None):
    with open("dockets.json") as f:
        config = json.load(f)
    dockets = config["dockets"]
    if docket_id is None:
        return dockets[0]
    for d in dockets:
        if d["id"] == docket_id:
            return d
    raise ValueError(f"Docket '{docket_id}' not found in dockets.json")


def extract_doc_id_from_text(paragraphs):
    non_blank = [p for p in paragraphs if p.strip()]
    for p in non_blank[:15]:
        m = re.search(STF_PATTERN, p, re.IGNORECASE)
        if m:
            return re.sub(r'(?<=STF-)\s+|\s+(?=-)', '', m.group(1).upper())
    return None


def extract_doc_id_from_filename(filename):
    basename = Path(filename).stem
    m = re.search(STF_PATTERN, basename, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return basename


def extract_staff(doc_id):
    m = re.search(r'STF-([A-Z]{2,4})-', doc_id, re.IGNORECASE)
    return m.group(1).upper() if m else ""


def parse_qa_docx(docx_bytes, zip_entry_name):
    try:
        doc = Document(io.BytesIO(docx_bytes))
    except Exception as e:
        print(f"    ERROR reading docx {zip_entry_name}: {e}")
        return None

    paragraphs = [p.text.strip() for p in doc.paragraphs]
    full_text = "\n".join(p for p in paragraphs if p)

    has_q = bool(re.search(r'(?i)question\s*:', full_text))
    has_r = bool(re.search(r'(?i)response\s*:', full_text))
    if not has_q or not has_r:
        return None

    doc_id = extract_doc_id_from_text(paragraphs)
    if not doc_id:
        doc_id = extract_doc_id_from_filename(zip_entry_name)

    basename = Path(zip_entry_name).name
    is_pd = bool(re.match(r'PD\s+STF-[A-Z]{2,4}', basename, re.IGNORECASE))

    q_match = re.search(r'(?im)^question\s*:?\s*$', full_text)
    r_match = re.search(r'(?im)^response\s*:?\s*$', full_text)

    if q_match and r_match and q_match.start() < r_match.start():
        question = full_text[q_match.end():r_match.start()].strip()
        response = full_text[r_match.end():].strip()
    else:
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
    qa, conf, other = [], [], []
    for name in zip_names:
        basename = Path(name).name
        lower = basename.lower()

        if "drsum" in lower:
            continue
        if lower.endswith(".pdf") and "verification" in lower:
            continue
        if "trade secret" in lower or (
            "confidential" in lower
            and not re.search(r'STF-[A-Z]{2,4}-\d+-\d+', basename, re.IGNORECASE)
        ):
            conf.append(name)
        elif (name.endswith(".docx")
              and re.search(r'STF-[A-Z]{2,4}-\d+-\d+', basename, re.IGNORECASE)
              and "attachment" not in lower):
            qa.append(name)
        else:
            other.append(name)
    return qa, conf, other


def main():
    parser = argparse.ArgumentParser(description="Re-parse cached ZIPs for a docket")
    parser.add_argument("--docket", default=None, help="Docket ID from dockets.json (default: first entry)")
    args = parser.parse_args()

    docket_config = load_docket_config(args.docket)
    docket_id = docket_config["id"]

    docket_dir = OUTPUT_DIR / f"docket_{docket_id}"
    zips_dir = docket_dir / "zips"
    manifest_json = docket_dir / "manifest.json"
    db_csv = docket_dir / "qa.csv"
    db_sqlite = docket_dir / "qa.sqlite"

    print(f"Docket: {docket_id} — {docket_config['label']}")

    with open(manifest_json) as f:
        manifest = json.load(f)

    zip_meta = {}
    for entry in manifest:
        if entry.get("status") == "ok":
            key = f"{entry['doc_id_api']}_{entry['zip_filename']}"
            zip_meta[key] = entry

    rows = []
    zip_files = sorted(zips_dir.glob("*.zip"))
    print(f"Re-parsing {len(zip_files)} cached ZIPs...\n")

    for zip_path in zip_files:
        api_id = int(zip_path.name.split("_")[0])
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

            doc_id_clean = re.sub(r'\s+', '', result["doc_id"])
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

    with open(db_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {db_csv}")

    if db_sqlite.exists():
        db_sqlite.unlink()
    con = sqlite3.connect(db_sqlite)
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
    con.execute("INSERT INTO qa_fts(rowid, staff, doc_id, question, response) SELECT id, staff, doc_id, question, response FROM qa_responses")
    con.commit()
    con.close()
    print(f"Wrote {db_sqlite}")


if __name__ == "__main__":
    main()
