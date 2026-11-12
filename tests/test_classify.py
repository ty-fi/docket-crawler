from docket_crawler.classify import classify_members


def test_classify_real_layout():
    members = [
        "DKT 55378 STF-JKA-1 PD/",
        "DKT 55378 STF-JKA-1 PD/DRSUM 55378 STF-JKA-1 (stamp).docx",
        "DKT 55378 STF-JKA-1 PD/JKA-1-4/",
        "DKT 55378 STF-JKA-1 PD/JKA-1-4/PD STF-JKA-1-4 Attachment.docx",
        "DKT 55378 STF-JKA-1 PD/JKA-1-4/PD STF-JKA-1-4.docx",
        "DKT 55378 STF-JKA-1 PD/STF-JKA-1-2.docx",
        "DKT 55378 STF-JKA-1 PD/verification & attestation.pdf",
        "DKT 55378 STF-JKA-1 PD/Trade Secret Affidavit.pdf",
        "DKT 55378 STF-JKA-1 PD/Trade Secret Workpapers.xlsx",
        "DKT 55378 STF-JKA-1 PD/STF-JKA-1-2 Attachment.xlsx",
        "__MACOSX/._STF-JKA-1-2.docx",
        "DKT 55378 STF-JKA-1 PD/~$F-JKA-1-2.docx",
    ]
    c = classify_members(members, "STF")
    assert c.qa == [
        "DKT 55378 STF-JKA-1 PD/JKA-1-4/PD STF-JKA-1-4.docx",
        "DKT 55378 STF-JKA-1 PD/STF-JKA-1-2.docx",
    ]
    assert c.attachments == [
        "DKT 55378 STF-JKA-1 PD/JKA-1-4/PD STF-JKA-1-4 Attachment.docx",
        "DKT 55378 STF-JKA-1 PD/STF-JKA-1-2 Attachment.xlsx",
    ]
    assert c.confidential == ["DKT 55378 STF-JKA-1 PD/Trade Secret Workpapers.xlsx"]
    assert len(c.skipped) == 3  # DRSUM + verification + affidavit


def test_procedural_files_and_assertion_letters():
    members = [
        "DKT 44280 STF-PIA-1 PD/STF-PIA-1 Affidavit TS.pdf",
        "DKT 44280 STF-ASR-6/STF-ASR-6 Afffidavit.pdf",
        "DKT 44280 STF-WG-9 Supp PD/STF-WG-9 Verification Letter TS.docx",
        "DKT 44280 STF-LA-11/STF-LA-11.DR-44280.docx",
        "DKT 44280 STF-PIA-39 PD/Open Notebook.onetoc2",
        "DKT 44280 STF-LA-1 PD/STF-LA-1-1 Assertion Letter.docx",
        "DKT 44280 STF-LA-1 PD/STF-LA-1-1.docx",
    ]
    c = classify_members(members, "STF")
    assert len(c.skipped) == 5
    assert c.qa == ["DKT 44280 STF-LA-1 PD/STF-LA-1-1.docx"]
    assert c.attachments == ["DKT 44280 STF-LA-1 PD/STF-LA-1-1 Assertion Letter.docx"]
