"""Extracting question/response pairs from data-request response documents (.docx)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .ids import RequestId, first_id

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A label at the start of a line: "Question:", "QUESTION" (alone), "Response :" ...
# Requires a colon or end of line so "Questions regarding..." does not count.
_LABEL_LINE = r"(?im)^[ \t]*{word}[ \t]*(?::[ \t]*|$)"
_QUESTION_LINE = re.compile(_LABEL_LINE.format(word="question"))
_RESPONSE_LINE = re.compile(_LABEL_LINE.format(word="(?:response|answer)"))
_QUESTION_ANY = re.compile(r"(?i)\bquestion\s*:")
_RESPONSE_ANY = re.compile(r"(?i)\bresponse\s*:")


@dataclass
class ParsedQA:
    request_id: Optional[RequestId]
    question: str
    response: str


def _table_lines(table: Table) -> Iterator[str]:
    for row in table.rows:
        cells: List[str] = []
        prev = None
        for cell in row.cells:
            # Merged cells repeat the same underlying element; emit them once.
            if cell._tc is prev:
                continue
            prev = cell._tc
            cells.append(re.sub(r"\s+", " ", cell.text).strip())
        if any(cells):
            yield " | ".join(cells)


def document_lines(doc) -> List[str]:
    """Non-blank text lines of a document body in reading order, tables rendered as
    ``cell | cell`` rows."""
    lines: List[str] = []
    for el in doc.element.body.iterchildren():
        if el.tag == f"{_W_NS}p":
            text = Paragraph(el, doc).text.strip()
            if text:
                lines.append(text)
        elif el.tag == f"{_W_NS}tbl":
            lines.extend(_table_lines(Table(el, doc)))
    return lines


def _split(full_text: str) -> Optional[Tuple[str, str]]:
    """Split document text into (question, response).

    Prefer labels at the start of a line, so a stray "response:" inside the question
    text does not cut it short. Fall back to the first inline label."""
    q = _QUESTION_LINE.search(full_text)
    r = None
    if q:
        r = _RESPONSE_LINE.search(full_text, q.end())
    if q and r:
        return full_text[q.end():r.start()].strip(), full_text[r.end():].strip()

    q = _QUESTION_ANY.search(full_text)
    if not q:
        return None
    r = _RESPONSE_ANY.search(full_text, q.end())
    if not r:
        return None
    return full_text[q.end():r.start()].strip(), full_text[r.end():].strip()


def parse_qa_text(lines: List[str], prefix: str) -> Optional[ParsedQA]:
    full_text = "\n".join(lines)
    split = _split(full_text)
    if split is None:
        return None
    question, response = split
    header = "\n".join(lines[:15])
    return ParsedQA(request_id=first_id(header, prefix), question=question, response=response)


def parse_qa_docx(data: bytes, prefix: str) -> Optional[ParsedQA]:
    doc = Document(io.BytesIO(data))
    return parse_qa_text(document_lines(doc), prefix)
