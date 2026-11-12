import email.message
import urllib.error

import pytest

from docket_crawler.psc_client import PSCClient, SiteRefusingError, parse_retry_after


def http_error(code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://x", code, "err", headers, None)


class FakeClient(PSCClient):
    """PSCClient whose responses come from a script; records sleeps instead of sleeping."""

    def __init__(self, script, **kw):
        self.sleeps = []
        super().__init__(min_interval=0, sleep=self.sleeps.append, **kw)
        self.script = list(script)
        self.calls = 0

    def _open(self, url):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def test_parse_retry_after():
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:30 GMT", now=1445412480.0) == 30.0
    assert parse_retry_after("garbage") is None
    assert parse_retry_after(None) is None


def test_retries_then_succeeds_and_honors_retry_after():
    c = FakeClient([http_error(503, "45"), http_error(500), b"ok"], backoff=5)
    assert c.get("u") == b"ok"
    assert c.sleeps == [45.0, 10.0]  # Retry-After beats the 5s backoff; then 5*2


def test_gives_up_when_retry_after_is_too_long():
    c = FakeClient([http_error(429, "3600")])
    with pytest.raises(urllib.error.HTTPError):
        c.get("u")
    assert c.calls == 1


def test_404_is_not_retried_and_not_a_refusal():
    c = FakeClient([http_error(404)] * 5, max_refusals=2)
    for _ in range(3):
        with pytest.raises(urllib.error.HTTPError):
            c.get("u")
    assert c.calls == 3


def test_stops_after_consecutive_refusals():
    c = FakeClient([http_error(403), http_error(403), http_error(403)], max_refusals=3)
    for _ in range(2):
        with pytest.raises(urllib.error.HTTPError):
            c.get("u")
    with pytest.raises(SiteRefusingError):
        c.get("u")
    with pytest.raises(SiteRefusingError):  # no further requests are sent
        c.get("u")
    assert c.calls == 3


def test_success_resets_refusal_count():
    c = FakeClient([http_error(403), b"ok", http_error(403), b"ok"], max_refusals=2)
    with pytest.raises(urllib.error.HTTPError):
        c.get("u")
    assert c.get("u") == b"ok"
    with pytest.raises(urllib.error.HTTPError):
        c.get("u")
    assert c.get("u") == b"ok"


def test_persistent_429_counts_as_refusal():
    c = FakeClient([http_error(429)] * 2, retries=1, backoff=0, max_refusals=1)
    with pytest.raises(SiteRefusingError):
        c.get("u")
