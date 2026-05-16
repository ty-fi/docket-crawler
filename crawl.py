"""
Georgia PSC docket crawler.
Fetches filings for a configured docket, extracts Q&A pairs, builds a database.

Usage:
  python crawl.py                    # uses first docket in dockets.json
  python crawl.py --docket 55378
"""

import argparse
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

OUTPUT_DIR = Path("output")

DELAY_BETWEEN_PAGES = 2   # seconds between document-page fetches
DELAY_BETWEEN_ZIPS = 5    # seconds between ZIP downloads

HEADERS = {
    "User-Agent": "Mozilla/5.0 (research crawler; tylerjordanfitch@gmail.com)",
    "Referer": "https://psc.ga.gov/",
}

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


# ── HTTP helpers ─────────────────────────────────────────────────────────────
def fetch(url, binary=False):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read() if binary else resp.read().decode("utf-8")


# ── Step 1: collect all matching filings from the API ──────────────────────
def get_stf_filings(docket_config):
    docket_id = docket_config["id"]
    company_filter = docket_config["company_filter"]
    doc_pattern = docket_config["doc_pattern"]

    base = (
        "https://psc.ga.gov/search/service-facts-docket/"
        f"?docketId={docket_id}&sortDirection=ASC&sortColumn=Filed"
        "&searchText=&pageSize=50&pageNumber={page}"
    )

    print(f"Fetching filing list for docket {docket_id} from PSC API...")
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

    matches = []
    for item in all_items:
        companies = [c["companyName"] for c in item.get("companyDetailsVm", [])]
        desc = item.get("description", "")
        if any(company_filter in c for c in companies) and re.search(doc_pattern, desc):
            matches.append(item)

    print(f"\nFound {len(matches)} matching filings")
    return matches


# ── Step 2: get attachment download URLs from each document page ───────────
def get_attachment_urls(document_id):
    url = f"https://psc.ga.gov/search/facts-document/?documentId={document_id}"
    html = fetch(url)
    pattern = r'href="(https://services\.psc\.ga\.gov[^"]+DownloadFile[^"]+)"[^>]*>.*?([^\n<>]+\.(?:zip|pdf|docx|xlsx|xls|doc))'
    matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
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
    doc = Document(io.BytesIO(docx_bytes))
    paragraphs = [p.text.strip() for p in doc.paragraphs]
    full_text = "\n".join(p for p in paragraphs if p)

    if not re.search(r'(?i)question\s*:', full_text):
        return None
    if not re.search(r'(?i)response\s*:', full_text):
        return None

    doc_id = ""
    for p in [p for p in paragraphs if p][:15]:
        m = re.search(STF_PATTERN, p, re.IGNORECASE)
        if m:
            doc_id = re.sub(r'(?<=STF-)\s+|\s+(?=-)', '', m.group(1).upper())
            break
    if not doc_id:
        doc_id = next((p for p in paragraphs if p), "")

    q_match = re.search(r'(?im)^question\s*:?\s*$', full_text)
    r_match = re.search(r'(?im)^response\s*:?\s*$', full_text)

    if q_match and r_match and q_match.start() < r_match.start():
        question = full_text[q_match.end():r_match.start()].strip()
        response = full_text[r_match.end():].strip()
    else:
        parts = re.split(r'(?i)response\s*:', full_text, maxsplit=1)
        response = parts[1].strip() if len(parts) > 1 else ""
        q_parts = re.split(r'(?i)question\s*:', parts[0], maxsplit=1)
        question = q_parts[1].strip() if len(q_parts) > 1 else ""

    return {
        "doc_id": doc_id,
        "question": question,
        "response": response,
    }


# ── Step 5: classify files inside a ZIP ─────────────────────────────────────
def classify_zip_contents(zip_names):
    qa, conf, attach = [], [], []
    for name in zip_names:
        basename = Path(name).name
        lower = basename.lower()
        if "drsum" in lower:
            continue
        if "verification" in lower and name.endswith(".pdf"):
            continue
        if "confidential" in lower:
            conf.append(name)
        elif (name.endswith(".docx")
              and re.search(r'STF-[A-Z]{2,4}-\d+-\d+', basename, re.IGNORECASE)
              and "attachment" not in lower):
            qa.append(name)
        else:
            attach.append(name)
    return qa, conf, attach


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Crawl a Georgia PSC docket")
    parser.add_argument("--docket", default=None, help="Docket ID from dockets.json (default: first entry)")
    args = parser.parse_args()

    docket_config = load_docket_config(args.docket)
    docket_id = docket_config["id"]

    docket_dir = OUTPUT_DIR / f"docket_{docket_id}"
    zips_dir = docket_dir / "zips"
    extracted_dir = docket_dir / "extracted"
    db_csv = docket_dir / "qa.csv"
    db_sqlite = docket_dir / "qa.sqlite"
    manifest_json = docket_dir / "manifest.json"

    OUTPUT_DIR.mkdir(exist_ok=True)
    docket_dir.mkdir(exist_ok=True)
    zips_dir.mkdir(exist_ok=True)
    extracted_dir.mkdir(exist_ok=True)

    print(f"Docket: {docket_id} — {docket_config['label']}")

    filings = get_stf_filings(docket_config)

    rows = []
    manifest = []

    for filing_idx, filing in enumerate(filings):
        doc_id_api = filing["documentId"]
        description = filing["description"].strip().replace("\r", "").replace("\n", " ").replace("\t", " ")
        filed_date = filing["filedDateString"]
        print(f"\n[{filing_idx+1}/{len(filings)}] docId={doc_id_api} | {description}")

        time.sleep(DELAY_BETWEEN_PAGES)
        attachments_on_page = get_attachment_urls(doc_id_api)
        if not attachments_on_page:
            print(f"  WARNING: no attachments found on page for docId={doc_id_api}")
            manifest.append({"doc_id_api": doc_id_api, "description": description, "status": "no_attachments"})
            continue

        filing_dir = extracted_dir / f"{doc_id_api}"
        filing_dir.mkdir(exist_ok=True)

        for att_fname, att_url in attachments_on_page:
            if not att_fname.lower().endswith(".zip"):
                print(f"  Skipping non-ZIP attachment: {att_fname}")
                continue

            zip_path = zips_dir / f"{doc_id_api}_{att_fname}"
            time.sleep(DELAY_BETWEEN_ZIPS)
            zip_bytes = download_zip(att_url, zip_path)

            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            all_names = zf.namelist()
            qa_files, conf_files, other_files = classify_zip_contents(all_names)

            print(f"    ZIP contents: {len(qa_files)} Q&A, {len(conf_files)} confidentiality, {len(other_files)} other")

            zf.extractall(filing_dir)

            for qa_name in qa_files:
                qa_data = parse_qa_docx(zf.read(qa_name))
                if not qa_data:
                    print(f"    WARNING: could not parse Q&A from {qa_name}")
                    continue

                qa_basename = Path(qa_name).stem
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
        with open(db_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote {len(rows)} Q&A records to {db_csv}")

        if db_sqlite.exists():
            db_sqlite.unlink()
        con = sqlite3.connect(db_sqlite)
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
        print(f"Wrote SQLite database to {db_sqlite}")
    else:
        print("\nNo Q&A records extracted.")

    with open(manifest_json, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest to {manifest_json}")


if __name__ == "__main__":
    main()
