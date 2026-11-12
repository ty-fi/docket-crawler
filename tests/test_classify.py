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
    assert c.confidential == ["DKT 55378 STF-JKA-1 PD/Trade Secret Affidavit.pdf"]
    assert len(c.skipped) == 2  # DRSUM + verification
