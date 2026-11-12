from docket_crawler.ids import RequestId, find_ids, first_id, has_pd_marker, id_for_member


def test_normalizes_spacing_and_case():
    assert str(first_id("stf - jka -1 - 4", "STF")) == "STF-JKA-1-4"
    assert str(first_id("STF-PIA-3-1 .docx", "STF")) == "STF-PIA-3-1"


def test_item_boundary():
    # -1-1 must not match inside -1-10
    assert find_ids("STF-DEA-1-10 Attachment A.xlsx", "STF") == [RequestId("STF", "DEA", 1, 10)]


def test_pd_prefix_variants():
    assert str(first_id("PD STF-DEA-1-10 Attachment A.xlsx", "STF")) == "STF-DEA-1-10"
    assert str(first_id("PD-STF-DEA-1-10 Attachment B.xlsx", "STF")) == "STF-DEA-1-10"
    assert has_pd_marker("folder/PD STF-JKA-1-4.docx", "STF")
    assert has_pd_marker("PD-STF-JKA-1-4.docx", "STF")
    assert not has_pd_marker("STF-JKA-1-4.docx", "STF")
    assert has_pd_marker("STF-WG-1-2 PUBLIC DISCLOSURE.docx", "STF")
    assert has_pd_marker("STF-TIA-1-16 Attachments A-AP PD.docx", "STF")
    assert not has_pd_marker("STF-PIA-3-1 Updated.docx", "STF")
    assert not has_pd_marker("STF-PIA-3-1 pdf export.docx", "STF")


def test_set_level_names_do_not_match():
    assert first_id("DKT 55378 STF-JKA-1 PD", "STF") is None
    assert first_id("DRSUM 55378 STF-JKA-1 (stamp).docx", "STF") is None


def test_member_id_from_folder_without_prefix():
    rid = id_for_member("DKT 55378 STF-JKA-1 PD/JKA-1-4/Workpaper.xlsx", "STF")
    assert rid == RequestId("STF", "JKA", 1, 4)


def test_member_id_prefers_file_name():
    rid = id_for_member("STF-DEA-1-1/PD STF-DEA-1-10 Attachment.xlsx", "STF")
    assert str(rid) == "STF-DEA-1-10"


def test_member_top_level_folder_is_not_an_id():
    assert id_for_member("DKT 55378 STF-JKA-1 PD/notes.pdf", "STF") is None


def test_other_prefix():
    assert str(first_id("SC-ABC-2-3 response", "SC")) == "SC-ABC-2-3"
    assert first_id("STF-ABC-2-3", "SC") is None


def test_party_codes_with_digits_and_four_letters():
    assert first_id("DKT 44280 STF-NU1-2-3.docx", "STF") == RequestId("STF", "NU1", 2, 3)
    assert str(first_id("PD STF-PCMG-1-5 Attachment.xlsx", "STF")) == "STF-PCMG-1-5"
    assert str(id_for_member("DKT 44280 STF-NU1-2/NU1-2-3/Workpaper.xlsx", "STF")) == "STF-NU1-2-3"
    # A party code must start with a letter, and set-level names still don't match.
    assert first_id("STF-1AB-2-3", "STF") is None
    assert first_id("DKT 44280 STF-NU1-2 PD", "STF") is None


def test_space_between_prefix_and_party():
    assert str(first_id("STF BAI-1-1 Attachment Coyne A.xlsx", "STF")) == "STF-BAI-1-1"


def test_member_id_from_any_ancestor_folder():
    assert str(id_for_member("BAI-1-1/Coyne/Coyne workpapers.xlsx", "STF")) == "STF-BAI-1-1"
