"""Building the static site (data.json + SQLite + CSV downloads) from the per-docket CSVs."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import List

from .build import FIELDS, read_csv, write_csv
from .config import SITE_DIR, DocketConfig

STATIC_DIR = Path(__file__).parent / "site_static"

INT_FIELDS = {"set_no", "item_no", "response_seq", "document_id"}

# Fields the browser needs. Everything else stays in the CSV/SQLite downloads.
SITE_FIELDS = [
    "row_id", "docket_id", "doc_id", "party", "set_no", "item_no",
    "response_seq", "is_supplemental", "is_pd", "has_trade_secret", "filed_date",
    "document_id", "filing_description", "filing_url", "source_file", "source_url",
    "question", "response", "attachments", "confidential_files",
]


def _typed(row: dict) -> dict:
    return {k: (int(v) if k in INT_FIELDS and v != "" else v) for k, v in row.items()}


def write_sqlite(path: Path, rows: List[dict]) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    cols = ",\n".join(
        f"  {f} {'INTEGER' if f in INT_FIELDS else 'TEXT'}" + (" PRIMARY KEY" if f == "row_id" else "")
        for f in FIELDS
    )
    con.execute(f"CREATE TABLE qa_responses (\n{cols}\n)")
    for col in ("docket_id", "doc_id", "party", "filed_date"):
        con.execute(f"CREATE INDEX idx_{col} ON qa_responses({col})")
    con.executemany(
        f"INSERT INTO qa_responses ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
        [[r[f] for f in FIELDS] for r in rows],
    )
    con.execute("""
        CREATE VIRTUAL TABLE qa_fts USING fts5(
            doc_id, question, response, content='qa_responses', content_rowid='rowid'
        )
    """)
    con.execute("INSERT INTO qa_fts(rowid, doc_id, question, response) "
                "SELECT rowid, doc_id, question, response FROM qa_responses")
    con.commit()
    con.execute("VACUUM")
    con.close()


def build_site(dockets: List[DocketConfig], out_dir: Path = SITE_DIR) -> Path:
    rows: List[dict] = []
    included: List[DocketConfig] = []
    for d in dockets:
        if not d.csv_path.exists():
            print(f"  Skipping docket {d.id}: {d.csv_path} not found")
            continue
        docket_rows = [_typed(r) for r in read_csv(d.csv_path)]
        print(f"  Loaded {len(docket_rows)} records from docket {d.id}")
        rows.extend(docket_rows)
        included.append(d)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(STATIC_DIR, out_dir)

    payload = {
        "built": date.today().isoformat(),
        "latest_filing": max((r["filed_date"] for r in rows), default=None),
        "dockets": [
            {"id": d.id, "label": d.label, "subtitle": d.subtitle, "party_label": d.party_label}
            for d in included
        ],
        "rows": [{k: r[k] for k in SITE_FIELDS} for r in rows],
    }
    with open(out_dir / "data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    write_csv(out_dir / "qa.csv", rows)
    write_sqlite(out_dir / "qa.sqlite", rows)
    (out_dir / ".nojekyll").touch()

    print(f"  Wrote site to {out_dir}/ ({len(rows)} records, {len(included)} docket(s))")
    return out_dir
