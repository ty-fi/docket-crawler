"""HTTP client for the Georgia PSC website: throttled, with retries."""

from __future__ import annotations

import html
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlencode

USER_AGENT = "Mozilla/5.0 (research crawler; mail@tyfi.computer)"
SITE = "https://psc.ga.gov"
PAGE_SIZE = 50

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def filing_url(document_id: int) -> str:
    return f"{SITE}/search/facts-document/?documentId={document_id}"


class PSCClient:
    def __init__(self, min_interval: float = 2.0, retries: int = 4, timeout: float = 60.0,
                 backoff: float = 5.0):
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff
        self._last_request = 0.0
        self.headers = {"User-Agent": USER_AGENT, "Referer": f"{SITE}/"}

    # ── low level ───────────────────────────────────────────────────────────
    def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, url: str) -> bytes:
        last_err: Exception = RuntimeError("no attempts made")
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                if e.code not in RETRYABLE_STATUS:
                    raise
                last_err = e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
            if attempt < self.retries:
                delay = self.backoff * (2 ** attempt)
                print(f"    retry {attempt + 1}/{self.retries} in {delay:.0f}s: {last_err}")
                time.sleep(delay)
        raise last_err

    # ── API ─────────────────────────────────────────────────────────────────
    def list_filings(self, docket_id: str) -> List[dict]:
        def page_url(page: int) -> str:
            query = urlencode({
                "docketId": docket_id, "sortDirection": "ASC", "sortColumn": "Filed",
                "searchText": "", "pageSize": PAGE_SIZE, "pageNumber": page,
            })
            return f"{SITE}/search/service-facts-docket/?{query}"

        first = json.loads(self.get(page_url(1)))
        items = list(first["resultsItems"])
        pages = math.ceil(first["resultsCount"] / PAGE_SIZE)
        for page in range(2, pages + 1):
            items.extend(json.loads(self.get(page_url(page)))["resultsItems"])
        if len(items) != first["resultsCount"]:
            print(f"  WARNING: expected {first['resultsCount']} filings, got {len(items)}")
        return items

    def attachment_links(self, document_id: int) -> List[Tuple[str, str]]:
        """(filename, download_url) pairs listed on a filing's page."""
        page = self.get(filing_url(document_id)).decode("utf-8", errors="replace")
        return parse_attachment_links(page)

    def download(self, url: str, dest: Path) -> int:
        """Download to dest atomically. Returns the number of bytes written."""
        data = self.get(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        return len(data)


_LINK_RE = re.compile(
    r'href="(https://services\.psc\.ga\.gov[^"]*DownloadFile[^"]*)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_FILENAME_RE = re.compile(r"([^\n<>]+\.[A-Za-z0-9]{2,5})\s*$")


def parse_attachment_links(page_html: str) -> List[Tuple[str, str]]:
    seen: Dict[str, str] = {}
    for url, inner in _LINK_RE.findall(page_html):
        text = html.unescape(re.sub(r"<[^>]+>", " ", inner)).strip()
        m = _FILENAME_RE.search(text)
        if not m:
            continue
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        url = html.unescape(url)
        seen.setdefault(url, name)
    return [(name, url) for url, name in seen.items()]
