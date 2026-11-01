from __future__ import annotations

import httpx
import pytest

from thanos.config import Settings
from thanos.tools import web


@pytest.fixture(autouse=True)
def small_limit(monkeypatch):
    monkeypatch.setattr(web, "get_settings", lambda: Settings(max_tool_output_chars=400))


def test_web_search_formats_results(monkeypatch) -> None:
    monkeypatch.setattr(
        web,
        "search_web",
        lambda q, n: [{"title": "Believer", "url": "https://w/b", "snippet": "A song."}],
    )
    r = web.web_search("believer", max_results=50)
    assert r.ok and r.message == "1. Believer — https://w/b\n   A song."


def test_web_search_no_results_and_error(monkeypatch) -> None:
    monkeypatch.setattr(web, "search_web", lambda q, n: [])
    assert not web.web_search("zzz").ok

    def boom(q, n):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(web, "search_web", boom)
    r = web.web_search("x")
    assert not r.ok and "rate limited" in r.message


def _mock_get(monkeypatch, response: httpx.Response) -> list[str]:
    seen: list[str] = []

    def fake_get(url, **kw):
        seen.append(url)
        response.request = httpx.Request("GET", url)
        return response

    monkeypatch.setattr(web.httpx, "get", fake_get)
    return seen


HTML = """<html><head><title>Moon facts</title></head><body>
<nav>Home | About</nav>
<article><h1>The Moon</h1><p>The Moon is Earth's only natural satellite.
It orbits at an average distance of 384,400 km and is about one quarter the diameter of Earth.
</p><p>It formed about 4.5 billion years ago, not long after Earth itself.</p></article>
</body></html>"""


def test_fetch_extracts_main_text(monkeypatch) -> None:
    seen = _mock_get(
        monkeypatch, httpx.Response(200, html=HTML, headers={"content-type": "text/html"})
    )
    r = web.fetch_webpage("example.com/moon")
    assert seen == ["https://example.com/moon"]
    assert r.ok and "natural satellite" in r.message and "Home | About" not in r.message


def test_fetch_truncates(monkeypatch) -> None:
    long_html = "<html><body><article>" + "<p>word " * 3000 + "</article></body></html>"
    _mock_get(monkeypatch, httpx.Response(200, html=long_html))
    r = web.fetch_webpage("https://x.com")
    assert r.ok and "chars trimmed" in r.message and len(r.message) < 600


def test_fetch_http_error(monkeypatch) -> None:
    _mock_get(monkeypatch, httpx.Response(404, text="nope"))
    r = web.fetch_webpage("https://x.com/missing")
    assert not r.ok and "HTTP 404" in r.message


def test_fetch_non_html(monkeypatch) -> None:
    _mock_get(
        monkeypatch,
        httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
    )
    r = web.fetch_webpage("https://x.com/a.pdf")
    assert not r.ok and "Not a web page" in r.message
