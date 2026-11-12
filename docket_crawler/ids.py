"""Data-request identifiers such as ``STF-JKA-1-4`` (prefix-party-set-item)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath
from typing import List, Optional, Pattern


@dataclass(frozen=True)
class RequestId:
    prefix: str
    party: str
    set_no: int
    item_no: int

    def __str__(self) -> str:
        return f"{self.prefix}-{self.party}-{self.set_no}-{self.item_no}"


# The party code after the prefix: 2-4 characters starting with a letter, e.g. JKA, PCMG, NU1.
PARTY = r"[A-Za-z][A-Za-z0-9]{1,3}"


@lru_cache(maxsize=None)
def id_regex(prefix: str, prefix_optional: bool = False) -> Pattern[str]:
    """Matches IDs like ``STF-JKA-1-4`` or ``STF-NU1-2-3``, tolerating stray spaces, ``_`` separators, and
    a space or ``_`` instead of a dash, and a leading ``PD``. The trailing lookahead stops ``-1-1`` matching inside ``-1-10``."""
    p = rf"(?P<prefix>{re.escape(prefix)})(?:\s*[-_]\s*|\s+)"
    if prefix_optional:
        p = f"(?:{p})?"
    return re.compile(
        rf"(?<![A-Za-z0-9])(?P<pd>PD[\s_-]+)?{p}"
        rf"(?P<party>{PARTY})\s*[-_]\s*(?P<set>\d+)\s*[-_]\s*(?P<item>\d+)(?!\d)",
        re.IGNORECASE,
    )


def find_ids(text: str, prefix: str, prefix_optional: bool = False) -> List[RequestId]:
    return [
        RequestId(prefix.upper(), m.group("party").upper(), int(m.group("set")), int(m.group("item")))
        for m in id_regex(prefix, prefix_optional).finditer(text)
    ]


def first_id(text: str, prefix: str, prefix_optional: bool = False) -> Optional[RequestId]:
    ids = find_ids(text, prefix, prefix_optional)
    return ids[0] if ids else None


def has_pd_marker(filename: str, prefix: str = "") -> bool:
    """True if a file name marks a public-disclosure (redacted) version: a leading
    ``PD`` ("PD STF-JKA-1-4.docx", "PD-STF-..."), ``PD`` as its own word
    ("STF-TIA-1-16 Attachments A-AP PD.docx"), or "PUBLIC DISCLOSURE" spelled out
    ("STF-WG-1-2 PUBLIC DISCLOSURE.docx"). ``PD`` must be upper case, so words like
    "Updated" don't count."""
    stem = PurePosixPath(filename).name.rsplit(".", 1)[0]
    return (
        re.search(r"(?:^|[\s_-])PD(?:$|[\s_-])", stem.strip()) is not None
        or "PUBLIC DISCLOSURE" in stem.upper()
    )


def id_for_member(member: str, prefix: str) -> Optional[RequestId]:
    """The request a ZIP member belongs to: from its file name, else from its folder name.

    Folders are often named without the prefix (``JKA-1-4/``), so the prefix is optional
    there. Folders are checked nearest first (``BAI-1-1/Coyne/file.pdf`` belongs to
    BAI-1-1); the top-level folder names the whole set (``DKT 55378 STF-JKA-1 PD``) and
    never matches the full four-part pattern."""
    path = PurePosixPath(member)
    rid = first_id(path.name, prefix)
    if rid:
        return rid
    for folder in reversed(path.parts[:-1]):  # nearest folder first
        rid = first_id(folder, prefix, prefix_optional=True)
        if rid:
            return rid
    return None
