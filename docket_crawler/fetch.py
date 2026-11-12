"""Finding a docket's data-request filings and caching their downloadable files."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .config import DocketConfig
from .psc_client import PSCClient, filing_url

# Attachments we download and parse. Everything else is recorded with its link only.
DOWNLOAD_EXTENSIONS = (".zip", ".docx")

MANIFEST_VERSION = 2


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def iso_date(item: dict) -> str:
    raw = item.get("filedDate") or ""
    try:
        return datetime.fromisoformat(raw[:19]).date().isoformat()
    except ValueError:
        # Fall back to the M/D/YYYY display string.
        return datetime.strptime(item["filedDateString"], "%m/%d/%Y").date().isoformat()


def cache_name(document_id: int, filename: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", filename).strip()
    return f"{document_id}_{safe}"


def filing_matches(item: dict, cfg: DocketConfig) -> bool:
    companies = [c.get("companyName", "") for c in item.get("companyDetailsVm") or []]
    if not any(cfg.company_filter.lower() in c.lower() for c in companies):
        return False
    return re.search(cfg.filing_regex, item.get("description") or "", re.IGNORECASE) is not None


# ── manifest ────────────────────────────────────────────────────────────────
def load_manifest(cfg: DocketConfig) -> Dict[int, dict]:
    if not cfg.manifest_path.exists():
        return {}
    with open(cfg.manifest_path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict) or raw.get("version") != MANIFEST_VERSION:
        print(f"  Manifest at {cfg.manifest_path} is an old format; starting fresh.")
        return {}
    return {f["document_id"]: f for f in raw["filings"]}


def save_manifest(cfg: DocketConfig, filings: Dict[int, dict]) -> None:
    ordered = sorted(filings.values(), key=lambda f: (f["filed_date"], f["document_id"]))
    payload = {"version": MANIFEST_VERSION, "docket_id": cfg.id, "filings": ordered}
    cfg.dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.manifest_path.with_name(cfg.manifest_path.name + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, cfg.manifest_path)


# ── fetch ───────────────────────────────────────────────────────────────────
def _new_entry(item: dict, links: List[tuple]) -> dict:
    doc_id = item["documentId"]
    return {
        "document_id": doc_id,
        "description": normalize_space(item.get("description", "")),
        "filed_date": iso_date(item),
        "url": filing_url(doc_id),
        "attachments": [
            {"filename": name, "url": url, "cache_name": cache_name(doc_id, name)}
            for name, url in links
        ],
        "status": "ok" if links else "no_attachments",
    }


def fetch_docket(cfg: DocketConfig, client: PSCClient, refresh_pages: bool = False) -> List[str]:
    """Update the docket's manifest and file cache. Returns a list of error messages."""
    print(f"\n== Docket {cfg.id}: {cfg.label}")
    manifest = load_manifest(cfg)
    items = client.list_filings(cfg.id)
    matched = [i for i in items if filing_matches(i, cfg)]
    print(f"  {len(items)} filings on docket, {len(matched)} match "
          f"company {cfg.company_filter!r} and pattern {cfg.filing_regex!r}")

    errors: List[str] = []
    current: Dict[int, dict] = {}
    downloaded = 0
    for n, item in enumerate(matched, 1):
        doc_id = item["documentId"]
        entry = manifest.get(doc_id)
        try:
            if entry is None or refresh_pages or entry["status"] != "ok":
                label = "new" if entry is None else "refresh"
                print(f"  [{n}/{len(matched)}] {label}: {doc_id} {normalize_space(item['description'])}")
                entry = _new_entry(item, client.attachment_links(doc_id))
            else:
                # Keep the cached attachment list; refresh metadata that is free to update.
                entry["description"] = normalize_space(item.get("description", ""))
                entry["filed_date"] = iso_date(item)

            for att in entry["attachments"]:
                if not att["filename"].lower().endswith(DOWNLOAD_EXTENSIONS):
                    continue
                dest = cfg.cache_dir / att["cache_name"]
                if dest.exists():
                    continue
                size = client.download(att["url"], dest)
                downloaded += 1
                print(f"      downloaded {att['filename']} ({size:,} bytes)")
        except Exception as e:  # keep going; the filing is retried on the next run
            msg = f"docket {cfg.id} document {doc_id}: {type(e).__name__}: {e}"
            print(f"      ERROR {msg}")
            errors.append(msg)
            if entry is None:
                entry = _new_entry(item, [])
            entry["status"] = "error"
        current[doc_id] = entry
        save_manifest(cfg, {**manifest, **current})

    dropped = set(manifest) - set(current)
    if dropped:
        print(f"  {len(dropped)} filings no longer listed, removed from manifest: {sorted(dropped)}")
    save_manifest(cfg, current)
    print(f"  {downloaded} files downloaded, {len(errors)} errors")
    return errors
