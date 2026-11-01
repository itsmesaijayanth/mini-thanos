"""YouTube tools: find a video with yt-dlp (no API key) and open it in the default browser."""

from __future__ import annotations

import logging
import webbrowser
from urllib.parse import quote_plus

from thanos.tools.base import ToolResult
from thanos.tools.registry import tool

log = logging.getLogger(__name__)

WATCH_URL = "https://www.youtube.com/watch?v={id}"
SEARCH_URL = "https://www.youtube.com/results?search_query={q}"


def search_youtube(query: str, limit: int = 1) -> list[dict[str, str]]:
    """Return [{id, title, url}] for the top `limit` YouTube results."""
    from yt_dlp import YoutubeDL  # imported lazily: slow import, only needed here

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for entry in (info or {}).get("entries") or []:
        vid = entry.get("id")
        if vid:
            results.append(
                {"id": vid, "title": entry.get("title") or "", "url": WATCH_URL.format(id=vid)}
            )
    return results


def _open(url: str) -> bool:
    return webbrowser.open(url, new=2)


@tool(category="media", direct=True)
def play_youtube(query: str) -> ToolResult:
    """Play the best matching YouTube video right away in the browser.
    Use when the user says play, watch, or put on a song or video.
    If they say "search YouTube for ..." or want to see options, use open_youtube_search.

    Args:
        query: What to search for, e.g. "believer imagine dragons" or "lofi hip hop radio".
    """
    if not query:
        return ToolResult.fail("query is empty; ask the user what to play.")
    try:
        results = search_youtube(query, limit=1)
    except Exception as e:
        log.warning("YouTube search failed for %r: %s", query, e)
        results = []

    if not results:
        url = SEARCH_URL.format(q=quote_plus(query))
        _open(url)
        return ToolResult.fail(
            f"Could not find a direct video for '{query}'; opened YouTube search results instead.",
            url=url,
        )

    top = results[0]
    if not _open(top["url"]):
        return ToolResult.fail("Could not open the browser.", url=top["url"])
    return ToolResult.success(f"Playing '{top['title']}' on YouTube.", **top)


@tool(category="media", direct=True)
def open_youtube_search(query: str) -> ToolResult:
    """Open the YouTube search results page so the user can browse (nothing auto-plays).
    Use when the user says "search YouTube for ...", "find/show ... on YouTube", or wants
    to pick from options. If they say "play" or "watch", use play_youtube instead.

    Args:
        query: Search terms, e.g. "python tutorials".
    """
    url = SEARCH_URL.format(q=quote_plus(query))
    if not _open(url):
        return ToolResult.fail("Could not open the browser.", url=url)
    return ToolResult.success(f"Opened YouTube search for '{query}'.", url=url)
