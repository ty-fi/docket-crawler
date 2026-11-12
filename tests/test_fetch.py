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


# ── fetch_docket with a fake client ─────────────────────────────────────────
import json

import pytest

from docket_crawler import config
from docket_crawler.fetch import fetch_docket, load_manifest, save_manifest
from docket_crawler.psc_client import SiteRefusingError


class StubClient:
    def __init__(self, filings, refuse_on=()):
        self.filings = filings
        self.refuse_on = set(refuse_on)
        self.pages, self.downloaded = [], []
        self.requests_made = 0
        self.downloads = 0

    def list_filings(self, docket_id):
        return self.filings

    def attachment_links(self, document_id):
        if document_id in self.refuse_on:
            raise SiteRefusingError("refused")
        self.pages.append(document_id)
        return [(f"set{document_id}.zip", f"https://dl/{document_id}")]

    def download(self, url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"zip")
        self.downloaded.append(url)
        self.downloads += 1
        return 3


def filing(doc_id, date="2024-01-01"):
    return {"documentId": doc_id, "description": f"DKT 1 STF-DEA-{doc_id} Responses",
            "filedDate": f"{date}T00:00:00", "filedDateString": "",
            "companyDetailsVm": [{"companyName": "Georgia Power Company"}]}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    return config.DocketConfig(id="1", label="D1", subtitle="", company_filter="Georgia Power")


def seed_manifest(cfg, *doc_ids):
    save_manifest(cfg, {d: {
        "document_id": d, "description": "x", "filed_date": "2023-01-01", "url": "u", "status": "ok",
        "attachments": [{"filename": f"set{d}.zip", "url": f"https://dl/{d}", "cache_name": f"{d}_set{d}.zip"}],
    } for d in doc_ids})


def test_cold_cache_downloads_only_new_filings(cfg):
    seed_manifest(cfg, 1)                      # known filing, file NOT cached
    client = StubClient([filing(1), filing(2)])
    assert fetch_docket(cfg, client) == []
    assert client.pages == [2]                 # only the new filing's page
    assert client.downloaded == ["https://dl/2"]
    assert set(load_manifest(cfg)) == {1, 2}


def test_download_all_fills_the_cache(cfg):
    seed_manifest(cfg, 1)
    client = StubClient([filing(1)])
    fetch_docket(cfg, client, download_all=True)
    assert client.pages == []
    assert client.downloaded == ["https://dl/1"]


def test_download_cap_defers_new_filings(cfg):
    client = StubClient([filing(1), filing(2), filing(3)])
    fetch_docket(cfg, client, max_downloads=2)
    assert client.downloaded == ["https://dl/1", "https://dl/2"]
    assert set(load_manifest(cfg)) == {1, 2}   # 3 is picked up next run
    client2 = StubClient([filing(1), filing(2), filing(3)])
    fetch_docket(cfg, client2)
    assert client2.downloaded == ["https://dl/3"]


def test_refusal_stops_and_keeps_manifest(cfg):
    seed_manifest(cfg, 1)
    client = StubClient([filing(1), filing(2), filing(3)], refuse_on={2})
    with pytest.raises(SiteRefusingError):
        fetch_docket(cfg, client)
    assert set(load_manifest(cfg)) == {1}      # nothing dropped, 2 and 3 retried next run
