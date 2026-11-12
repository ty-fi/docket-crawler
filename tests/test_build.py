import io
import json
import zipfile

from docx import Document

from docket_crawler import build, config
from docket_crawler.build import build_docket, number_responses


def qa_docx(doc_id: str, question: str, response: str) -> bytes:
    doc = Document()
    for t in [doc_id, "Question:", question, "Response:", response]:
        doc.add_paragraph(t)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_number_responses_marks_supplementals():
    rows = [
        {"docket_id": "1", "doc_id": "STF-A-1-1", "party": "A", "set_no": 1, "item_no": 1,
         "filed_date": "2024-01-12", "document_id": 9, "source_member": "x"},
        {"docket_id": "1", "doc_id": "STF-A-1-1", "party": "A", "set_no": 1, "item_no": 1,
         "filed_date": "2023-12-21", "document_id": 5, "source_member": "x"},
        {"docket_id": "1", "doc_id": "STF-A-1-2", "party": "A", "set_no": 1, "item_no": 2,
         "filed_date": "2023-12-21", "document_id": 5, "source_member": "y"},
    ]
    number_responses(rows)
    assert [(r["doc_id"], r["filed_date"], r["response_seq"], r["is_supplemental"]) for r in rows] == [
        ("STF-A-1-1", "2023-12-21", 1, "no"),
        ("STF-A-1-1", "2024-01-12", 2, "yes"),
        ("STF-A-1-2", "2023-12-21", 1, "no"),
    ]


def test_build_docket_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    cfg = config.DocketConfig(id="1", label="D1", subtitle="", company_filter="X")
    cfg.cache_dir.mkdir(parents=True)

    with zipfile.ZipFile(cfg.cache_dir / "100_set1.zip", "w") as zf:
        zf.writestr("DKT 1 STF-DEA-1 PD/DRSUM 1 STF-DEA-1.docx", b"ignored")
        zf.writestr("DKT 1 STF-DEA-1 PD/STF-DEA-1-1/STF-DEA-1-1.docx", qa_docx("STF-DEA-1-1", "Q1", "R1"))
        zf.writestr("DKT 1 STF-DEA-1 PD/STF-DEA-1-1/PD STF-DEA-1-1 Attachment A.xlsx", b"a")
        zf.writestr("DKT 1 STF-DEA-1 PD/STF-DEA-1-10/PD STF-DEA-1-10.docx", qa_docx("STF-DEA-1-10", "Q10", "R10"))
        zf.writestr("DKT 1 STF-DEA-1 PD/STF-DEA-1-10/PD-STF-DEA-1-10 Attachment B.xlsx", b"b")
        zf.writestr("DKT 1 STF-DEA-1 PD/DEA-1-11/Workpaper.xlsx", b"c")
        zf.writestr("DKT 1 STF-DEA-1 PD/DEA-1-11/STF-DEA-1-11.docx", qa_docx("STF-DEA-1-11", "Q11", "R11"))
    (cfg.cache_dir / "200_STF-DEA-1-1 Supplemental.docx").write_bytes(qa_docx("STF-DEA-1-1", "Q1", "R1 updated"))

    manifest = {"version": 2, "docket_id": "1", "filings": [
        {"document_id": 100, "description": "STF-DEA-1", "filed_date": "2023-12-01",
         "url": "u100", "status": "ok",
         "attachments": [{"filename": "set1.zip", "url": "d100", "cache_name": "100_set1.zip"}]},
        {"document_id": 200, "description": "STF-DEA-1-1 supp", "filed_date": "2024-01-05",
         "url": "u200", "status": "ok",
         "attachments": [{"filename": "STF-DEA-1-1 Supplemental.docx", "url": "d200",
                          "cache_name": "200_STF-DEA-1-1 Supplemental.docx"}]},
    ]}
    cfg.manifest_path.write_text(json.dumps(manifest))

    rows, problems = build_docket(cfg)
    by = {(r["doc_id"], r["response_seq"]): r for r in rows}

    assert [(r["doc_id"], r["response_seq"]) for r in rows] == [
        ("STF-DEA-1-1", 1), ("STF-DEA-1-1", 2), ("STF-DEA-1-10", 1), ("STF-DEA-1-11", 1),
    ]
    # Attachments go to exactly the right request (no -1-1 / -1-10 bleed).
    assert by[("STF-DEA-1-1", 1)]["attachments"] == "PD STF-DEA-1-1 Attachment A.xlsx"
    assert by[("STF-DEA-1-10", 1)]["attachments"] == "PD-STF-DEA-1-10 Attachment B.xlsx"
    # Folder without the prefix still assigns its files.
    assert by[("STF-DEA-1-11", 1)]["attachments"] == "Workpaper.xlsx"
    assert by[("STF-DEA-1-10", 1)]["is_pd"] == "yes"
    assert by[("STF-DEA-1-1", 2)]["is_supplemental"] == "yes"
    assert by[("STF-DEA-1-1", 2)]["response"] == "R1 updated"
    assert by[("STF-DEA-1-1", 2)]["source_url"] == "d200"
    assert len({r["row_id"] for r in rows}) == len(rows)
    assert problems == []

    # Round-trips through CSV with every field.
    build.write_csv(tmp_path / "qa.csv", rows)
    back = build.read_csv(tmp_path / "qa.csv")
    assert [r["row_id"] for r in back] == [r["row_id"] for r in rows]
    assert back[1]["response"] == "R1 updated"
