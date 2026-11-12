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
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

USER_AGENT = "Mozilla/5.0 (research crawler; mail@tyfi.computer)"
SITE = "https://psc.ga.gov"
PAGE_SIZE = 50

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# Responses that mean "you are being refused", as opposed to a missing page.
REFUSAL_STATUS = {401, 403, 429}
# Never wait longer than this for one Retry-After; give up on the request instead.
MAX_RETRY_AFTER = 600.0


class SiteRefusingError(RuntimeError):
    """The site keeps refusing our requests; stop crawling for this run."""


def filing_url(document_id: int) -> str:
    return f"{SITE}/search/facts-document/?documentId={document_id}"


def parse_retry_after(value: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


class PSCClient:
    def __init__(self, min_interval: float = 2.0, retries: int = 4, timeout: float = 60.0,
                 backoff: float = 5.0, max_refusals: int = 3, sleep=time.sleep):
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff
        self.max_refusals = max_refusals
        self._sleep = sleep
        self._last_request = 0.0
        self._refusals = 0  # consecutive requests that ended in a refusal
        self.requests_made = 0
        self.downloads = 0
        self.headers = {"User-Agent": USER_AGENT, "Referer": f"{SITE}/"}

    # ── low level ───────────────────────────────────────────────────────────
    def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_request = time.monotonic()

    def _open(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def _refused(self, err: Exception) -> None:
        self._refusals += 1
        if self._refusals >= self.max_refusals:
            raise SiteRefusingError(
                f"{self._refusals} requests in a row were refused (last: {err}); stopping this run"
            ) from err

    def get(self, url: str) -> bytes:
        if self._refusals >= self.max_refusals:
            raise SiteRefusingError("site is refusing requests; not sending more this run")
        last_err: Exception = RuntimeError("no attempts made")
        for attempt in range(self.retries + 1):
            self._throttle()
            self.requests_made += 1
            retry_after = None
            try:
                data = self._open(url)
                self._refusals = 0
                return data
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code not in RETRYABLE_STATUS:
                    if e.code in REFUSAL_STATUS:
                        self._refused(e)
                    raise
                retry_after = parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
                if retry_after is not None and retry_after > MAX_RETRY_AFTER:
                    print(f"    server asked us to wait {retry_after:.0f}s; giving up on this request")
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
            if attempt < self.retries:
                delay = max(self.backoff * (2 ** attempt), retry_after or 0)
                print(f"    retry {attempt + 1}/{self.retries} in {delay:.0f}s: {last_err}")
                self._sleep(delay)
        if isinstance(last_err, urllib.error.HTTPError) and last_err.code in REFUSAL_STATUS:
            self._refused(last_err)
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
        self.downloads += 1
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
