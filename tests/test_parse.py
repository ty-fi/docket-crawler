import io

from docx import Document

from docket_crawler.parse import parse_qa_docx, parse_qa_lines, parse_qa_text


def make_docx(build) -> bytes:
    doc = Document()
    build(doc)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_standard_layout():
    data = make_docx(lambda d: [d.add_paragraph(t) for t in [
        "STF-DEA-1-12", "Question:", "What is X?", "Response:", "X is 5.", "Second paragraph.",
    ]])
    qa = parse_qa_docx(data, "STF")
    assert str(qa.request_id) == "STF-DEA-1-12"
    assert qa.question == "What is X?"
    assert qa.response == "X is 5.\nSecond paragraph."


def test_tables_are_included_in_order():
    def build(d):
        d.add_paragraph("STF-PIA-2-1")
        d.add_paragraph("Question:")
        d.add_paragraph("Provide the table.")
        d.add_paragraph("Response:")
        d.add_paragraph("See below.")
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text = "Year", "MW"
        t.cell(1, 0).text, t.cell(1, 1).text = "2024", "150"
        d.add_paragraph("End of response.")
    qa = parse_qa_docx(make_docx(build), "STF")
    assert qa.response == "See below.\nYear | MW\n2024 | 150\nEnd of response."


def test_merged_table_cells_emitted_once():
    def build(d):
        d.add_paragraph("Question:")
        d.add_paragraph("Q")
        d.add_paragraph("Response:")
        t = d.add_table(rows=1, cols=3)
        t.cell(0, 0).merge(t.cell(0, 1)).text = "Merged"
        t.cell(0, 2).text = "C"
    qa = parse_qa_docx(make_docx(build), "STF")
    assert qa.response == "Merged | C"


def test_inline_response_word_in_question_does_not_split():
    lines = ["STF-JKA-2-3", "Question:", "Explain the Company's response: to Staff in STF-JKA-1-1.",
             "Response:", "Answer here."]
    qa = parse_qa_text(lines, "STF")
    assert qa.question == "Explain the Company's response: to Staff in STF-JKA-1-1."
    assert qa.response == "Answer here."


def test_label_with_text_on_same_line():
    qa = parse_qa_text(["STF-LA-1-2", "Question: What?", "Response: That."], "STF")
    assert (qa.question, qa.response) == ("What?", "That.")


def test_questions_word_is_not_a_label():
    lines = ["Question:", "Questions remain about X.", "Response:", "Fine."]
    qa = parse_qa_text(lines, "STF")
    assert qa.question == "Questions remain about X."


def test_answer_label_after_preamble():
    lines = ["Data Request No. STF-JKA-3-4", "BASIS FOR THE ASSERTION THAT THE", "Some preamble.",
             "STF-JKA-3-4", "Question:", "Provide X.", "Answer:", "See attachment."]
    qa = parse_qa_text(lines, "STF")
    assert str(qa.request_id) == "STF-JKA-3-4"
    assert (qa.question, qa.response) == ("Provide X.", "See attachment.")


def test_no_question_label():
    lines = ["STF-PIA-29-26", "For Hurricane Helene, provide the travel logs.", "Response:", "See STF-PIA-29-24."]
    qa = parse_qa_text(lines, "STF")
    assert (qa.question, qa.response) == ("For Hurricane Helene, provide the travel logs.", "See STF-PIA-29-24.")


def test_cover_letter_is_not_a_response():
    assert parse_qa_text(["February 5, 2026", "Dear Ms. Tanner:", "Enclosed is a supplemental filing."], "STF") is None


def test_falls_back_to_inline_labels():
    qa = parse_qa_text(["STF-LA-1-2 Question: What? Response: That."], "STF")
    assert (qa.question, qa.response) == ("What?", "That.")


def test_no_structure_returns_none():
    assert parse_qa_text(["Just a memo."], "STF") is None
    assert parse_qa_text(["Question:", "Q only"], "STF") is None


def test_combined_document_is_split_per_request():
    lines = ["STF-LA-9-1", "Question:", "Q one", "Response:", "R one",
             "STF-LA-9-2", "Question:", "Q two", "Response:", "R two", "See STF-LA-9-1 above.",
             "PD STF-LA-9-3", "Question:", "Q three", "Response:", "R three"]
    out = parse_qa_lines(lines, "STF")
    assert [(str(q.request_id), q.question, q.response) for q in out] == [
        ("STF-LA-9-1", "Q one", "R one"),
        ("STF-LA-9-2", "Q two", "R two\nSee STF-LA-9-1 above."),
        ("STF-LA-9-3", "Q three", "R three"),
    ]


def test_id_alone_on_a_line_in_a_normal_document_does_not_split():
    lines = ["STF-PIA-29-26", "Question:", "Provide logs.", "Response:", "See the response to",
             "STF-PIA-29-24", "for the billed time."]
    out = parse_qa_lines(lines, "STF")
    assert len(out) == 1 and out[0].response == "See the response to\nSTF-PIA-29-24\nfor the billed time."
