from docket_crawler.config import DocketConfig
from docket_crawler.fetch import cache_name, filing_matches, iso_date
from docket_crawler.psc_client import parse_attachment_links

CFG = DocketConfig(id="55378", label="L", subtitle="", company_filter="Georgia Power")


def item(desc, company="Georgia Power Company"):
    return {"description": desc, "companyDetailsVm": [{"companyName": company}]}


def test_filing_matches():
    assert filing_matches(item("DKT 55378 - STF-JKA-1 Data Request Responses"), CFG)
    assert not filing_matches(item("Data Request from Commission Staff (STF-PIA-1)", "Staff"), CFG)
    assert not filing_matches(item("DKT 55378 2023 IRP Update Filing"), CFG)


def test_iso_date():
    assert iso_date({"filedDate": "2023-10-27T00:00:00", "filedDateString": "10/27/2023"}) == "2023-10-27"
    assert iso_date({"filedDate": "", "filedDateString": "1/5/2024"}) == "2024-01-05"


def test_cache_name_is_filesystem_safe():
    assert cache_name(1, "a/b:c.zip") == "1_a_b_c.zip"


def test_parse_attachment_links():
    html = '''
      <a href="https://services.psc.ga.gov/api/v1/External/Public/Get/Document/DownloadFile/216531/97761"><i class="fas fa-download"></i>&nbsp;&nbsp;
          dkt_55378_stf-jka-1_pd.zip</a>
      <a href="https://example.com/other">not a download</a>
      <a href="https://services.psc.ga.gov/x/DownloadFile/1/2">Report &amp; Data.pdf</a>
    '''
    assert parse_attachment_links(html) == [
        ("dkt_55378_stf-jka-1_pd.zip",
         "https://services.psc.ga.gov/api/v1/External/Public/Get/Document/DownloadFile/216531/97761"),
        ("Report & Data.pdf", "https://services.psc.ga.gov/x/DownloadFile/1/2"),
    ]
