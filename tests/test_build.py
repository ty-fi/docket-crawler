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
        zf.writestr("DKT 1 STF-DEA-1 PD/STF-DEA-1-12.docx", qa_docx("STF-DEA-1-12", "Q12", "R12"))
        zf.writestr("DKT 1 STF-DEA-1 PD/STF-DAE-1-12 Attachment.xlsx", b"typo")  # party-code typo
        doc = Document()
        for t in ["STF-DEA-1-20", "Question:", "Q20", "Response:", "Cost is REDACTED.",
                  "STF-DEA-1-21", "Question:", "Q21", "Response:", "R21"]:
            doc.add_paragraph(t)
        buf = io.BytesIO(); doc.save(buf)
        zf.writestr("DKT 1 STF-DEA-1 PD/STF-DEA-1-20 - 21.docx", buf.getvalue())
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
        ("STF-DEA-1-12", 1), ("STF-DEA-1-20", 1), ("STF-DEA-1-21", 1),
    ]
    # A combined document becomes one row per request; REDACTED text marks it PD.
    assert (by[("STF-DEA-1-20", 1)]["response"], by[("STF-DEA-1-20", 1)]["is_pd"]) == ("Cost is REDACTED.", "yes")
    assert (by[("STF-DEA-1-21", 1)]["response"], by[("STF-DEA-1-21", 1)]["is_pd"]) == ("R21", "no")
    # A typo'd attachment goes to the one response with the same set and item numbers.
    assert by[("STF-DEA-1-12", 1)]["attachments"] == "STF-DAE-1-12 Attachment.xlsx"
    assert "attachments named STF-DAE-1-12" in by[("STF-DEA-1-12", 1)]["parse_warning"]
    # Attachments go to exactly the right request (no -1-1 / -1-10 bleed).
    assert by[("STF-DEA-1-1", 1)]["attachments"] == "PD STF-DEA-1-1 Attachment A.xlsx"
    assert by[("STF-DEA-1-10", 1)]["attachments"] == "PD-STF-DEA-1-10 Attachment B.xlsx"
    # Folder without the prefix still assigns its files.
    assert by[("STF-DEA-1-11", 1)]["attachments"] == "Workpaper.xlsx"
    assert by[("STF-DEA-1-10", 1)]["is_pd"] == "yes"
    # Public response text, but its attachment has a PD (redacted) version.
    assert (by[("STF-DEA-1-1", 1)]["is_pd"], by[("STF-DEA-1-1", 1)]["has_trade_secret"]) == ("no", "yes")
    assert by[("STF-DEA-1-11", 1)]["has_trade_secret"] == "no"
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


def test_has_trade_secret_signals():
    from docket_crawler.build import has_trade_secret
    assert has_trade_secret(True, [], "STF", "", "")                                   # PD response
    assert has_trade_secret(False, ["PD STF-DEA-1-1 Attachment A.xlsx"], "STF", "")     # PD attachment
    assert has_trade_secret(False, ["PD-STF-DEA-1-10 Attachment B.xlsx"], "STF", "")
    assert has_trade_secret(False, [], "STF", "See TS STF-DEA-1-1 Attachment A.")       # cites TS doc
    assert has_trade_secret(False, [], "STF", "refer to TS_Zip_AllResponses.zip")
    assert not has_trade_secret(False, ["STF-DEA-1-1 Attachment.xlsx"], "STF", "Costs and benefits.")
    assert has_trade_secret(False, ["STF-TIA-1-16 Attachments A-AP PD.docx"], "STF", "")
    assert has_trade_secret(False, ["STF-WG-9-1 Attachment A PUBLIC DISCLOSURE.xlsx"], "STF", "")
    assert has_trade_secret(False, ["STF-LA-1-1 Assertion Letter.docx"], "STF", "")
    assert not has_trade_secret(False, ["STF-LA-1-1 UPDATED Attachment.xlsx"], "STF", "")
    assert not has_trade_secret(False, [], "STF", "The TSA and ts values")              # not a TS doc ref


def test_cold_cache_keeps_rows_from_existing_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    cfg = config.DocketConfig(id="1", label="D1", subtitle="", company_filter="X")
    cfg.cache_dir.mkdir(parents=True)
    for doc, name in [(100, "STF-DEA-1-1.docx"), (200, "STF-DEA-1-2.docx")]:
        (cfg.cache_dir / f"{doc}_{name}").write_bytes(qa_docx(f"STF-DEA-1-{doc // 100}", "Q", f"R{doc}"))
    manifest = {"version": 2, "docket_id": "1", "filings": [
        {"document_id": d, "description": "d", "filed_date": f"2024-01-0{i}", "url": "u", "status": "ok",
         "attachments": [{"filename": n, "url": "x", "cache_name": f"{d}_{n}"}]}
        for i, (d, n) in enumerate([(100, "STF-DEA-1-1.docx"), (200, "STF-DEA-1-2.docx")], 1)
    ]}
    cfg.manifest_path.write_text(json.dumps(manifest))

    first, _ = build_docket(cfg)
    build.write_csv(cfg.csv_path, first)

    (cfg.cache_dir / "100_STF-DEA-1-1.docx").unlink()     # cache evicted for filing 100
    second, problems = build_docket(cfg)
    assert [r["row_id"] for r in second] == [r["row_id"] for r in first]
    assert [r["response"] for r in second] == ["R100", "R200"]
    assert problems == []
