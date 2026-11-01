from __future__ import annotations

import pytest

from thanos.tools import youtube


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(youtube, "_open", lambda url: urls.append(url) or True)
    return urls


def test_play_opens_top_result(monkeypatch, opened) -> None:
    monkeypatch.setattr(
        youtube,
        "search_youtube",
        lambda q, limit=1: [{"id": "abc", "title": "Believer", "url": youtube.WATCH_URL.format(id="abc")}],
    )
    r = youtube.play_youtube("believer")
    assert r.ok and "Believer" in r.message
    assert opened == ["https://www.youtube.com/watch?v=abc"]


def test_play_falls_back_to_search_page(monkeypatch, opened) -> None:
    def fail(q, limit=1):
        raise RuntimeError("network down")

    monkeypatch.setattr(youtube, "search_youtube", fail)
    r = youtube.play_youtube("some song")
    assert not r.ok
    assert opened == ["https://www.youtube.com/results?search_query=some+song"]


def test_search_parses_yt_dlp_entries(monkeypatch) -> None:
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            assert url == "ytsearch2:lofi" and download is False
            return {"entries": [{"id": "1", "title": "A"}, {"title": "no id"}]}

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    assert youtube.search_youtube("lofi", limit=2) == [
        {"id": "1", "title": "A", "url": "https://www.youtube.com/watch?v=1"}
    ]


def test_open_search(opened) -> None:
    r = youtube.open_youtube_search("python tutorials")
    assert r.ok and opened[0].endswith("python+tutorials")
