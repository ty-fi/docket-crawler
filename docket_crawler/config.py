"""Loading and validating dockets.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .ids import PARTY

DEFAULT_CONFIG_PATH = Path("dockets.json")
OUTPUT_DIR = Path("output")
SITE_DIR = Path("site")


@dataclass(frozen=True)
class DocketConfig:
    id: str
    label: str
    subtitle: str
    company_filter: str
    # Prefix of the data-request IDs to extract, e.g. "STF" for IDs like STF-JKA-1-4.
    request_prefix: str = "STF"
    # What the 2-4 letter code after the prefix means (shown as a column header).
    party_label: str = "Staff"
    # Regex a filing's description must match to be crawled. Defaults to "<prefix>-<party code>-".
    description_pattern: Optional[str] = None

    @property
    def filing_regex(self) -> str:
        return self.description_pattern or rf"{re.escape(self.request_prefix)}-{PARTY}-"

    @property
    def dir(self) -> Path:
        return OUTPUT_DIR / f"docket_{self.id}"

    @property
    def cache_dir(self) -> Path:
        return self.dir / "cache"

    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    @property
    def csv_path(self) -> Path:
        return self.dir / "qa.csv"


_KNOWN_KEYS = {
    "id", "label", "subtitle", "company_filter",
    "request_prefix", "party_label", "description_pattern",
    "doc_pattern",  # legacy name for description_pattern
}


def _parse_docket(raw: dict) -> DocketConfig:
    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise ValueError(f"Unknown keys in docket {raw.get('id')!r}: {sorted(unknown)}")
    for key in ("id", "label", "company_filter"):
        if not raw.get(key):
            raise ValueError(f"Docket entry missing required key {key!r}: {raw}")
    docket_id = str(raw["id"])
    if not docket_id.isdigit():
        raise ValueError(f"Docket id must be numeric, got {docket_id!r}")
    pattern = raw.get("description_pattern") or raw.get("doc_pattern")
    if pattern:
        re.compile(pattern)
    prefix = raw.get("request_prefix", "STF")
    if not re.fullmatch(r"[A-Za-z]+", prefix):
        raise ValueError(f"request_prefix must be letters only, got {prefix!r}")
    return DocketConfig(
        id=docket_id,
        label=raw["label"],
        subtitle=raw.get("subtitle", ""),
        company_filter=raw["company_filter"],
        request_prefix=prefix.upper(),
        party_label=raw.get("party_label", "Staff"),
        description_pattern=pattern,
    )


def load_dockets(path: Path = DEFAULT_CONFIG_PATH) -> List[DocketConfig]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    dockets = [_parse_docket(d) for d in raw["dockets"]]
    ids = [d.id for d in dockets]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate docket ids in {path}")
    if not dockets:
        raise ValueError(f"No dockets configured in {path}")
    return dockets


def select_dockets(dockets: List[DocketConfig], docket_id: Optional[str]) -> List[DocketConfig]:
    """All dockets when docket_id is None, otherwise just the named one."""
    if docket_id is None:
        return dockets
    for d in dockets:
        if d.id == docket_id:
            return [d]
    raise ValueError(f"Docket {docket_id!r} not found in dockets.json")
