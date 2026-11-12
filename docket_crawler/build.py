"""Turning cached filing files into Q&A records (one CSV per docket)."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Iterable, List, Tuple

from .classify import classify_members
from .config import DocketConfig
from .fetch import load_manifest
from .ids import first_id, has_pd_marker, id_for_member
from .parse import parse_qa_docx

FIELDS = [
    "row_id", "docket_id", "doc_id", "party", "set_no", "item_no",
    "response_seq", "is_supplemental", "is_pd", "has_trade_secret",
    "filed_date", "document_id", "filing_description", "filing_url",
    "source_file", "source_url", "source_member",
    "question", "response",
    "attachments", "confidential_files", "parse_warning",
]

ListSep = "; "

# References to trade-secret documents in response text: "See TS STF-DEA-1-1 Attachment A",
# "TS_Zip_AllResponses", "TS-STF-DEA-1-15 Attachment". Case-sensitive on purpose.
_TS_REFERENCE = re.compile(r"\bTS[\s_-]+[A-Z0-9(\"“]")


def has_trade_secret(is_pd: bool, attachments: List[str], prefix: str, *texts: str) -> bool:
    """Whether a trade-secret version of some of this response's material was filed.

    "PD" (public disclosure) marks the redacted public version of a document whose
    trade-secret version was filed confidentially. Signals: the response document is
    itself PD, one of its attachments is PD, or the text cites a "TS ..." document."""
    return (
        is_pd
        or any(has_pd_marker(a, prefix) or a.upper().startswith(("PD ", "PD-", "PD_")) for a in attachments)
        or any(_TS_REFERENCE.search(t or "") for t in texts)
    )


def _row_id(*parts: object) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:12]


def _open_source(path: Path, filename: str) -> Tuple[List[str], Callable[[str], bytes]]:
    """Member names and a reader for a cached file: a ZIP archive or a single .docx."""
    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        members = zf.namelist()
        # Unpack nested ZIPs one level deep; members are addressed as "outer.zip/inner".
        nested: Dict[str, zipfile.ZipFile] = {}
        for m in list(members):
            if m.lower().endswith(".zip"):
                inner = zipfile.ZipFile(io.BytesIO(zf.read(m)))
                nested[m] = inner
                members.remove(m)
                members.extend(f"{m}/{n}" for n in inner.namelist())

        def read(member: str) -> bytes:
            for outer, inner in nested.items():
                if member.startswith(outer + "/"):
                    return inner.read(member[len(outer) + 1:])
            return zf.read(member)

        return members, read
    data = path.read_bytes()
    return [filename], lambda _m: data


def _rows_for_source(cfg: DocketConfig, filing: dict, att: dict, path: Path) -> Tuple[List[dict], List[str]]:
    rows: List[dict] = []
    notes: List[str] = []
    members, read = _open_source(path, att["filename"])
    groups = classify_members(members, cfg.request_prefix)

    attachments_by_id: Dict[str, List[str]] = defaultdict(list)
    for m in groups.attachments:
        rid = id_for_member(m, cfg.request_prefix)
        if rid:
            attachments_by_id[str(rid)].append(PurePosixPath(m).name)
        else:
            notes.append(f"unmatched attachment {m}")
    confidential = ListSep.join(PurePosixPath(m).name for m in groups.confidential)

    seen_ids = set()
    for member in groups.qa:
        name = PurePosixPath(member).name
        try:
            parsed = parse_qa_docx(read(member), cfg.request_prefix)
        except Exception as e:
            notes.append(f"unreadable {member}: {type(e).__name__}: {e}")
            continue
        if parsed is None:
            notes.append(f"no Question/Response structure in {member}")
            continue

        file_id = first_id(name, cfg.request_prefix)
        # Prefer the file name: where they disagree in real filings, the ID typed in the
        # document header is a copy-paste leftover from another request (e.g. a file
        # STF-DEA-2-3.docx whose header says STF-DEA-2-1). Flag the disagreement.
        rid = file_id or parsed.request_id
        warning = ""
        if parsed.request_id and file_id and parsed.request_id != file_id:
            warning = f"document header says {parsed.request_id}"
        if not parsed.question or not parsed.response:
            warning = "; ".join(filter(None, [warning, "empty question or response"]))

        related = sorted(attachments_by_id.get(str(rid), []))
        is_pd = has_pd_marker(name, cfg.request_prefix)
        seen_ids.add(str(rid))
        rows.append({
            "row_id": _row_id(cfg.id, filing["document_id"], att["filename"], member),
            "docket_id": cfg.id,
            "doc_id": str(rid),
            "party": rid.party,
            "set_no": rid.set_no,
            "item_no": rid.item_no,
            "is_pd": "yes" if is_pd else "no",
            "has_trade_secret": "yes" if has_trade_secret(
                is_pd, related, cfg.request_prefix, parsed.question, parsed.response) else "no",
            "filed_date": filing["filed_date"],
            "document_id": filing["document_id"],
            "filing_description": filing["description"],
            "filing_url": filing["url"],
            "source_file": att["filename"],
            "source_url": att["url"],
            "source_member": member,
            "question": parsed.question,
            "response": parsed.response,
            "attachments": ListSep.join(related),
            "confidential_files": confidential,
            "parse_warning": warning,
        })

    orphan = [k for k in attachments_by_id if k not in seen_ids]
    for k in orphan:
        notes.append(f"attachments for {k} but no Q&A document: {attachments_by_id[k]}")
    return rows, notes


def _sort_key(row: dict) -> tuple:
    return (row["party"], int(row["set_no"]), int(row["item_no"]),
            row["filed_date"], int(row["document_id"]), row["source_member"])


def number_responses(rows: List[dict]) -> None:
    """Number repeat answers to the same request in filing order; later ones are supplemental."""
    rows.sort(key=_sort_key)
    counts: Dict[Tuple[str, str], int] = defaultdict(int)
    for row in rows:
        key = (row["docket_id"], row["doc_id"])
        counts[key] += 1
        row["response_seq"] = counts[key]
        row["is_supplemental"] = "yes" if counts[key] > 1 else "no"


def _previous_rows(cfg: DocketConfig) -> Dict[int, List[dict]]:
    """Rows from the existing CSV, by filing, for filings whose files aren't cached."""
    if not cfg.csv_path.exists():
        return {}
    by_filing: Dict[int, List[dict]] = defaultdict(list)
    for row in read_csv(cfg.csv_path):
        by_filing[int(row["document_id"])].append(row)
    return by_filing


def build_docket(cfg: DocketConfig) -> Tuple[List[dict], List[str]]:
    """Parse every filing whose files are cached. A filing with missing files keeps its
    rows from the existing CSV, so a cold cache never drops data."""
    print(f"\n== Docket {cfg.id}: parsing cached files")
    filings = load_manifest(cfg)
    if not filings:
        raise SystemExit(f"No manifest for docket {cfg.id}; run `fetch` first.")
    previous = _previous_rows(cfg)
    rows: List[dict] = []
    problems: List[str] = []
    parsed = kept = 0
    stale_schema = False
    for filing in sorted(filings.values(), key=lambda f: (f["filed_date"], f["document_id"])):
        doc_id = filing["document_id"]
        sources = [a for a in filing["attachments"] if a["filename"].lower().endswith((".zip", ".docx"))]
        missing = [a for a in sources if not (cfg.cache_dir / a["cache_name"]).exists()]

        if missing and previous.get(doc_id):
            for old in previous[doc_id]:
                stale_schema |= any(f not in old for f in FIELDS)
                rows.append({f: old.get(f, "") for f in FIELDS})
            kept += 1
            continue
        for a in missing:
            if filing["status"] == "ok":
                problems.append(f"{doc_id}: missing cached file {a['cache_name']} and no earlier rows")

        for att in sources:
            path = cfg.cache_dir / att["cache_name"]
            if att in missing:
                continue
            try:
                new_rows, notes = _rows_for_source(cfg, filing, att, path)
            except zipfile.BadZipFile as e:
                problems.append(f"{doc_id}: bad archive {att['filename']}: {e}")
                continue
            rows.extend(new_rows)
            problems.extend(f"{doc_id} {att['filename']}: {n}" for n in notes)
        parsed += 1
    number_responses(rows)
    if stale_schema:
        problems.append("kept rows from an older CSV schema; new columns are blank for them. "
                        "Run `fetch --download-all` then `parse` to rebuild every filing.")
    print(f"  {len(rows)} Q&A records: {parsed} filings parsed, {kept} kept from the existing CSV "
          f"(files not cached)")
    return rows, problems


def write_csv(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> List[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
