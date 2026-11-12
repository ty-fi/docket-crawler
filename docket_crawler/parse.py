"""Extracting question/response pairs from data-request response documents (.docx)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .ids import RequestId, first_id, id_regex

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

    if not q and not _QUESTION_ANY.search(full_text):
        # Some documents have no "Question:" label: request ID line, question text,
        # then "Response:". Take everything between the first line and the label.
        r = _RESPONSE_LINE.search(full_text)
        first_break = full_text.find("\n")
        if r and 0 < first_break < r.start():
            question = full_text[first_break:r.start()].strip()
            if question:
                return question, full_text[r.end():].strip()

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


def _heading_id(line: str, prefix: str) -> Optional[RequestId]:
    """The request ID if the line is nothing but an ID (optionally PD-marked)."""
    m = id_regex(prefix).fullmatch(line.strip())
    return first_id(line, prefix) if m else None


def split_combined(lines: List[str], prefix: str) -> Optional[List[List[str]]]:
    """Split a document holding several responses ("STF-LA-9-1 - 101.docx") at lines
    that are only a request ID. Returns None unless it is clearly such a document:
    at least two distinct ID headings from one request set, each section with its own
    Question/Response."""
    starts = [(i, rid) for i, line in enumerate(lines) if (rid := _heading_id(line, prefix))]
    ids = [rid for _, rid in starts]
    if len(set(ids)) < 2 or len(set(ids)) != len(ids):
        return None
    if len({(r.party, r.set_no) for r in ids}) != 1:
        return None
    sections = [lines[i:j] for (i, _), (j, _) in zip(starts, starts[1:] + [(len(lines), None)])]
    if not all(_split("\n".join(sec)) for sec in sections):
        return None
    return sections


def parse_qa_lines(lines: List[str], prefix: str) -> List[ParsedQA]:
    """One ParsedQA per response in the document (usually exactly one)."""
    sections = split_combined(lines, prefix)
    if sections:
        return [qa for sec in sections if (qa := parse_qa_text(sec, prefix))]
    qa = parse_qa_text(lines, prefix)
    return [qa] if qa else []


def parse_qa_docx_all(data: bytes, prefix: str) -> List[ParsedQA]:
    return parse_qa_lines(document_lines(Document(io.BytesIO(data))), prefix)


def parse_qa_docx(data: bytes, prefix: str) -> Optional[ParsedQA]:
    doc = Document(io.BytesIO(data))
    return parse_qa_text(document_lines(doc), prefix)
