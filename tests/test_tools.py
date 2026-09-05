"""The research tools: URL handling and the per-host throttle, offline."""

from __future__ import annotations

import os
import time

import pytest

from ampitools import tools


def test_iri_to_uri_encodes_non_ascii_and_keeps_existing_encoding():
    assert tools.iri_to_uri("https://ru.wikipedia.org/wiki/Дуров") == (
        "https://ru.wikipedia.org/wiki/%D0%94%D1%83%D1%80%D0%BE%D0%B2")
    already = "https://ru.wikipedia.org/wiki/%D0%94%D1%83%D1%80%D0%BE%D0%B2"
    assert tools.iri_to_uri(already) == already
    assert tools.iri_to_uri("https://example.org/a b?q=x y&z=Гриз#Дуров") == (
        "https://example.org/a%20b?q=x%20y&z=%D0%93%D1%80%D0%B8%D0%B7#%D0%94%D1%83%D1%80%D0%BE%D0%B2")
    # host names go through IDNA, ports and plain ASCII URLs pass through unchanged
    assert tools.iri_to_uri("https://пример.рф:8443/x") == "https://xn--e1afmkfd.xn--p1ai:8443/x"
    assert tools.iri_to_uri("https://www.google.com/search?q=a+b") == (
        "https://www.google.com/search?q=a+b")


def test_fetch_url_sends_an_ascii_request(monkeypatch, tmp_path):
    monkeypatch.setenv(tools.ENV_CACHE, str(tmp_path))
    monkeypatch.setattr(tools, "MIN_GAP_S", 0.0)
    seen: list[str] = []

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html><body><main><p>ok</p></main></body></html>"

    def fake_open(req, timeout=0):
        seen.append(req.full_url)
        req.full_url.encode("ascii")  # what http.client insists on
        return R()

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_open)
    assert tools.fetch_url("https://ru.wikipedia.org/wiki/Дуров") == "ok"
    assert seen == ["https://ru.wikipedia.org/wiki/%D0%94%D1%83%D1%80%D0%BE%D0%B2"]


@pytest.mark.skipif(tools.fcntl is None, reason="needs an advisory lock")
def test_throttle_spaces_requests_to_one_host(monkeypatch, tmp_path):
    monkeypatch.setenv(tools.ENV_CACHE, str(tmp_path))
    monkeypatch.setattr(tools, "MIN_GAP_S", 0.2)
    t0 = time.monotonic()
    tools._throttle("ru.wikipedia.org")
    tools._throttle("ru.wikipedia.org")
    tools._throttle("en.wikipedia.org")  # a different host has its own clock
    elapsed = time.monotonic() - t0
    assert 0.2 <= elapsed < 0.6
    assert os.path.exists(tmp_path / ".gap-ru.wikipedia.org")


def test_get_honours_retry_after_and_stops_on_a_real_error(monkeypatch, tmp_path):
    import urllib.error
    from email.message import Message

    monkeypatch.setenv(tools.ENV_CACHE, str(tmp_path))
    monkeypatch.setattr(tools, "MIN_GAP_S", 0.0)
    naps: list[float] = []
    monkeypatch.setattr(tools.time, "sleep", lambda s: naps.append(s))
    attempts = {"n": 0}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"fine"

    def fake_open(req, timeout=0):
        attempts["n"] += 1
        if attempts["n"] == 1:
            hdr = Message()
            hdr["Retry-After"] = "7"
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", hdr, None)
        return R()

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_open)
    assert tools._get("https://ru.wikipedia.org/x") == b"fine"
    assert attempts["n"] == 2 and naps == [7.0]

    def always_404(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "gone", Message(), None)

    monkeypatch.setattr(tools.urllib.request, "urlopen", always_404)
    with pytest.raises(urllib.error.HTTPError):
        tools._get("https://ru.wikipedia.org/y")
