"""
Georgia PSC Docket 55378 - STF-PIA crawler
Fetches all Georgia Power STF-PIA-* filings, extracts Q&A pairs, builds a database.
"""

import csv
import io
import json
import math
import re
import sqlite3
import time
import urllib.request
import zipfile
from pathlib import Path

from docx import Document

# ── Config ──────────────────────────────────────────────────────────────────
DOCKET_ID = 55378
OUTPUT_DIR = Path("output")
ZIPS_DIR = OUTPUT_DIR / "zips"
EXTRACTED_DIR = OUTPUT_DIR / "extracted"
DB_CSV = OUTPUT_DIR / "stf_pia_qa.csv"
DB_SQLITE = OUTPUT_DIR / "stf_pia_qa.sqlite"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"

DELAY_BETWEEN_PAGES = 2   # seconds between document-page fetches
DELAY_BETWEEN_ZIPS = 5    # seconds between ZIP downloads

HEADERS = {
    "User-Agent": "Mozilla/5.0 (research crawler; tylerjordanfitch@gmail.com)",
    "Referer": "https://psc.ga.gov/",
}


# ── HTTP helpers ─────────────────────────────────────────────────────────────
def fetch(url, binary=False):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read() if binary else resp.read().decode("utf-8")


# ── Step 1: collect all matching filings from the API ──────────────────────
def get_stf_filings():
    base = (
        "https://psc.ga.gov/search/service-facts-docket/"
        f"?docketId={DOCKET_ID}&sortDirection=ASC&sortColumn=Filed"
        "&searchText=&pageSize=50&pageNumber={page}"
    )

    print("Fetching filing list from PSC API...")
    first = json.loads(fetch(base.format(page=1)))
    total = first["resultsCount"]
    pages = math.ceil(total / 50)
    all_items = list(first["resultsItems"])
    print(f"  Total filings: {total}, pages: {pages}")

    for page in range(2, pages + 1):
        time.sleep(DELAY_BETWEEN_PAGES)
        data = json.loads(fetch(base.format(page=page)))
        all_items.extend(data["resultsItems"])
        print(f"  Page {page}/{pages}: {len(data['resultsItems'])} items")

    # Filter: Georgia Power + any STF-XXX- pattern in description
    matches = []
    for item in all_items:
        companies = [c["companyName"] for c in item.get("companyDetailsVm", [])]
        desc = item.get("description", "")
        if any("Georgia Power" in c for c in companies) and re.search(r'STF-[A-Z]{2,4}-', desc):
            matches.append(item)

    print(f"\nFound {len(matches)} STF-* filings from Georgia Power Company")
    return matches


# ── Step 2: get attachment download URLs from each document page ───────────
def get_attachment_urls(document_id):
    """Returns list of (filename, download_url) tuples for a document page."""
    url = f"https://psc.ga.gov/search/facts-document/?documentId={document_id}"
    html = fetch(url)
    pattern = r'href="(https://services\.psc\.ga\.gov[^"]+DownloadFile[^"]+)"[^>]*>.*?([^\n<>]+\.(?:zip|pdf|docx|xlsx|xls|doc))'
    matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
    # Clean up filenames (strip whitespace and icon chars)
    return [(fname.strip(), dl_url) for dl_url, fname in matches]


# ── Step 3: download and cache ZIP files ────────────────────────────────────
def download_zip(download_url, dest_path):
    if dest_path.exists():
        print(f"    [cached] {dest_path.name}")
        return dest_path.read_bytes()
    data = fetch(download_url, binary=True)
    dest_path.write_bytes(data)
    print(f"    [downloaded] {dest_path.name} ({len(data):,} bytes)")
    return data


# ── Step 4: parse Q&A from a .docx file ─────────────────────────────────────
def parse_qa_docx(docx_bytes):
    """
    Returns dict with keys: doc_id, question, response, raw_text
    or None if the file doesn't look like a Q&A response.
    """
    doc = Document(io.BytesIO(docx_bytes))
    paragraphs = [p.text.strip() for p in doc.paragraphs]
    # Remove blanks for scanning, but keep positions
    full_text = "\n".join(p for p in paragraphs if p)

    # Detect Q&A structure
    if "Question:" not in full_text and "QUESTION:" not in full_text.upper():
        return None
    if "Response:" not in full_text and "RESPONSE:" not in full_text.upper():
        return None

    # Find doc_id: look for STF-PIA-X-Y pattern in the first ~10 non-blank paragraphs
    doc_id = ""
    for p in [p for p in paragraphs if p][:10]:
        m = re.search(r'((?:PD\s+)?STF-PIA-\d+-\d+)', p)
        if m:
            doc_id = m.group(1)
            break
    if not doc_id:
        doc_id = next((p for p in paragraphs if p), "")

    # Split on Question / Response markers (case-insensitive)
    q_match = re.search(r'(?i)^question\s*:?\s*$', full_text, re.MULTILINE)
    r_match = re.search(r'(?i)^response\s*:?\s*$', full_text, re.MULTILINE)

    if q_match and r_match:
        question = full_text[q_match.end():r_match.start()].strip()
        response = full_text[r_match.end():].strip()
    else:
        # Fallback: split on inline "Question:" / "Response:"
        parts = re.split(r'(?i)response\s*:', full_text, maxsplit=1)
        response = parts[1].strip() if len(parts) > 1 else ""
        q_parts = re.split(r'(?i)question\s*:', parts[0], maxsplit=1)
        question = q_parts[1].strip() if len(q_parts) > 1 else ""

    return {
        "doc_id": doc_id,
        "question": question,
        "response": response,
        "raw_text": full_text,
    }


# ── Step 5: classify files inside a ZIP ─────────────────────────────────────
def classify_zip_contents(zip_names):
    """
    Returns:
      qa_files          - list of names that look like individual Q&A responses
      confidentiality   - list of names that look like confidentiality requests
      attachments       - everything else (xlsx, pdf, other docx)
    """
    qa, conf, attach = [], [], []
    for name in zip_names:
        basename = Path(name).name
        lower = basename.lower()
        if "drsum" in lower:
            continue  # summary doc, skip
        if "verification" in lower and name.endswith(".pdf"):
            continue  # signature page, skip
        if "confidential" in lower:
            conf.append(name)
        elif (name.endswith(".docx")
              and re.search(r'STF-PIA-\d+-\d+', basename)
              and "attachment" not in lower):
            qa.append(name)
        else:
            attach.append(name)
    return qa, conf, attach


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    ZIPS_DIR.mkdir(exist_ok=True)
    EXTRACTED_DIR.mkdir(exist_ok=True)

    filings = get_stf_filings()

    rows = []          # final Q&A records
    manifest = []      # per-filing summary for debugging

    for filing_idx, filing in enumerate(filings):
        doc_id_api = filing["documentId"]
        description = filing["description"].strip().replace("\r", "").replace("\n", " ").replace("\t", " ")
        filed_date = filing["filedDateString"]
        print(f"\n[{filing_idx+1}/{len(filings)}] docId={doc_id_api} | {description}")

        # Get attachment URLs from the document page
        time.sleep(DELAY_BETWEEN_PAGES)
        attachments_on_page = get_attachment_urls(doc_id_api)
        if not attachments_on_page:
            print(f"  WARNING: no attachments found on page for docId={doc_id_api}")
            manifest.append({"doc_id_api": doc_id_api, "description": description, "status": "no_attachments"})
            continue

        filing_dir = EXTRACTED_DIR / f"{doc_id_api}"
        filing_dir.mkdir(exist_ok=True)

        for att_fname, att_url in attachments_on_page:
            # Only process ZIP files (the PSC packages everything in ZIPs)
            if not att_fname.lower().endswith(".zip"):
                print(f"  Skipping non-ZIP attachment: {att_fname}")
                continue

            zip_path = ZIPS_DIR / f"{doc_id_api}_{att_fname}"
            time.sleep(DELAY_BETWEEN_ZIPS)
            zip_bytes = download_zip(att_url, zip_path)

            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            all_names = zf.namelist()
            qa_files, conf_files, other_files = classify_zip_contents(all_names)

            print(f"    ZIP contents: {len(qa_files)} Q&A, {len(conf_files)} confidentiality, {len(other_files)} other")

            # Extract all files to disk
            zf.extractall(filing_dir)

            # Parse each Q&A docx
            for qa_name in qa_files:
                qa_data = parse_qa_docx(zf.read(qa_name))
                if not qa_data:
                    print(f"    WARNING: could not parse Q&A from {qa_name}")
                    continue

                # Find attachments that share this Q&A's doc_id prefix
                qa_basename = Path(qa_name).stem  # e.g. "STF-PIA-1-1"
                related_attachments = [
                    Path(n).name for n in other_files
                    if qa_basename in Path(n).name
                ]

                rows.append({
                    "filing_api_id": doc_id_api,
                    "filing_description": description,
                    "filing_date": filed_date,
                    "doc_id": qa_data["doc_id"],
                    "zip_source": att_fname,
                    "question": qa_data["question"],
                    "response": qa_data["response"],
                    "attachment_filenames": "; ".join(related_attachments),
                    "confidentiality_files": "; ".join(Path(n).name for n in conf_files),
                })
                print(f"    + parsed: {qa_data['doc_id']} | attachments: {related_attachments}")

            manifest.append({
                "doc_id_api": doc_id_api,
                "description": description,
                "filed_date": filed_date,
                "zip_filename": att_fname,
                "qa_count": len(qa_files),
                "confidentiality_count": len(conf_files),
                "other_files": [Path(n).name for n in other_files],
                "status": "ok",
            })

    fieldnames = [
        "filing_api_id", "filing_description", "filing_date",
        "doc_id", "zip_source", "question", "response",
        "attachment_filenames", "confidentiality_files",
    ]

    if rows:
        # Write CSV
        with open(DB_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote {len(rows)} Q&A records to {DB_CSV}")

        # Write SQLite (Datasette-compatible)
        if DB_SQLITE.exists():
            DB_SQLITE.unlink()
        con = sqlite3.connect(DB_SQLITE)
        con.execute(f"""
            CREATE TABLE qa_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filing_api_id INTEGER,
                filing_description TEXT,
                filing_date TEXT,
                doc_id TEXT,
                zip_source TEXT,
                question TEXT,
                response TEXT,
                attachment_filenames TEXT,
                confidentiality_files TEXT
            )
        """)
        con.execute("CREATE INDEX idx_doc_id ON qa_responses(doc_id)")
        con.execute("CREATE INDEX idx_filing_date ON qa_responses(filing_date)")
        con.executemany(
            f"INSERT INTO qa_responses ({','.join(fieldnames)}) VALUES ({','.join('?' * len(fieldnames))})",
            [[r[f] for f in fieldnames] for r in rows],
        )
        con.commit()
        con.close()
        print(f"Wrote SQLite database to {DB_SQLITE}")
    else:
        print("\nNo Q&A records extracted.")

    # Write manifest
    with open(MANIFEST_JSON, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest to {MANIFEST_JSON}")


if __name__ == "__main__":
    main()
