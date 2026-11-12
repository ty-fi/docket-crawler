"""Sorting the members of a filing archive into Q&A documents, confidential files and attachments."""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import List

from .ids import first_id

QA_EXTENSIONS = (".docx",)


@dataclass
class Classified:
    qa: List[str] = field(default_factory=list)
    confidential: List[str] = field(default_factory=list)
    attachments: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


def _is_junk(member: str) -> bool:
    path = PurePosixPath(member)
    name = path.name
    return (
        member.endswith("/")
        or "__MACOSX" in path.parts
        or name.startswith(("~$", "._"))
        or name in {".DS_Store", "Thumbs.db"}
    )


def _is_procedural(lower: str) -> bool:
    """Filing paperwork that is neither a response nor a response's attachment:
    DR summaries, verifications and affidavits for the whole set, staff's original
    request letter (``STF-LA-11.DR-44280.docx``), and OneNote index files."""
    return (
        "drsum" in lower
        or "verification" in lower
        or re.search(r"\baff+idavit\b", lower) is not None
        or re.search(r"\.dr-\d+", lower) is not None
        or lower.endswith((".onetoc2", ".onepkg"))
    )


def classify_members(members: List[str], prefix: str) -> Classified:
    out = Classified()
    for member in members:
        if _is_junk(member):
            continue
        name = PurePosixPath(member).name
        lower = name.lower()
        if _is_procedural(lower):
            out.skipped.append(member)
        elif "trade secret" in lower or ("confidential" in lower and not first_id(name, prefix)):
            out.confidential.append(member)
        elif (lower.endswith(QA_EXTENSIONS) and "attachment" not in lower
              and "assertion letter" not in lower
              and first_id(name, prefix) is not None):
            out.qa.append(member)
        else:
            out.attachments.append(member)
    return out
