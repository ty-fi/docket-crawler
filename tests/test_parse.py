import io

from docx import Document

from docket_crawler.parse import parse_qa_docx, parse_qa_text


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


def test_falls_back_to_inline_labels():
    qa = parse_qa_text(["STF-LA-1-2 Question: What? Response: That."], "STF")
    assert (qa.question, qa.response) == ("What?", "That.")


def test_no_structure_returns_none():
    assert parse_qa_text(["Just a memo."], "STF") is None
    assert parse_qa_text(["Question:", "Q only"], "STF") is None
