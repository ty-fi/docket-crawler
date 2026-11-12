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


@lru_cache(maxsize=None)
def id_regex(prefix: str, prefix_optional: bool = False) -> Pattern[str]:
    """Matches IDs like ``STF-JKA-1-4``, tolerating stray spaces, ``_`` separators, and
    a leading ``PD``. The trailing lookahead stops ``-1-1`` matching inside ``-1-10``."""
    p = rf"(?P<prefix>{re.escape(prefix)})\s*[-_]\s*"
    if prefix_optional:
        p = f"(?:{p})?"
    return re.compile(
        rf"(?<![A-Za-z0-9])(?P<pd>PD[\s_-]+)?{p}"
        rf"(?P<party>[A-Za-z]{{2,4}})\s*[-_]\s*(?P<set>\d+)\s*[-_]\s*(?P<item>\d+)(?!\d)",
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


def has_pd_marker(filename: str, prefix: str) -> bool:
    """True if the file name starts with a ``PD`` marker before the request ID."""
    name = PurePosixPath(filename).name
    return re.match(rf"\s*PD[\s_-]+{re.escape(prefix)}\b", name, re.IGNORECASE) is not None


def id_for_member(member: str, prefix: str) -> Optional[RequestId]:
    """The request a ZIP member belongs to: from its file name, else from its folder name.

    Folders are often named without the prefix (``JKA-1-4/``), so the prefix is optional
    there. Only the immediate parent folder is used; the top-level folder names the whole
    set (``DKT 55378 STF-JKA-1 PD``) and never matches the full four-part pattern."""
    path = PurePosixPath(member)
    rid = first_id(path.name, prefix)
    if rid:
        return rid
    if len(path.parts) > 1:
        return first_id(path.parent.name, prefix, prefix_optional=True)
    return None
