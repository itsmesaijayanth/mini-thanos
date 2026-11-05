"""Basic web access: search (DuckDuckGo, no API key) and read a page's main text."""

from __future__ import annotations

import logging

import httpx

from thanos.config import get_settings
from thanos.tools.base import ToolResult, truncate_middle
from thanos.tools.registry import tool

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
FETCH_TIMEOUT = 15.0


def search_web(query: str, max_results: int) -> list[dict[str, str]]:
    """Return [{title, url, snippet}] from DuckDuckGo."""
    from ddgs import DDGS  # lazy: slow import

    hits = DDGS().text(query, max_results=max_results) or []
    return [
        {"title": h.get("title", ""), "url": h.get("href", ""), "snippet": h.get("body", "")}
        for h in hits
        if h.get("href")
    ]


def extract_text(html: str, url: str) -> tuple[str, str]:
    """Return (title, main readable text) of an HTML page."""
    import trafilatura

    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True) or ""
    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "") or ""
    return title, text.strip()


@tool(
    category="web",
    examples=["who won the last F1 race", "search the web for the latest iPhone price",
              "look up when the next eclipse is"],
)
def web_search(query: str, max_results: int = 5) -> ToolResult:
    """Search the web and get back titles, links and short snippets.
    Use for current events, facts you are unsure about, or to find pages to read.
    Then call fetch_webpage on the best link if you need the full content.

    Args:
        query: Search terms, e.g. "top songs this week billboard".
        max_results: How many results to return (1-10). Default 5.
    """
    n = max(1, min(max_results, 10))
    try:
        results = search_web(query, n)
    except Exception as e:
        return ToolResult.fail(f"Web search failed: {e}")
    if not results:
        return ToolResult.fail(f"No results for '{query}'. Try different words.")
    lines = [f"{i}. {r['title']} — {r['url']}\n   {r['snippet']}" for i, r in enumerate(results, 1)]
    return ToolResult.success("\n".join(lines))  # results already in message; no dup in data


@tool(
    category="web",
    examples=["read this page https://example.com", "what does this article say"],
)
def fetch_webpage(url: str) -> ToolResult:
    """Open a web page and return its main readable text (no ads/menus).
    Use after web_search, or when the user gives a link.

    Args:
        url: Full page address starting with http, e.g. "https://en.wikipedia.org/wiki/Moon".
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return ToolResult.fail(f"Page returned HTTP {e.response.status_code}: {url}")
    except httpx.HTTPError as e:
        return ToolResult.fail(f"Could not load {url}: {e}")

    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return ToolResult.fail(f"Not a web page (content-type {ctype or 'unknown'}): {url}")

    if "html" in ctype:
        title, text = extract_text(resp.text, str(resp.url))
    else:
        title, text = "", resp.text.strip()
    if not text:
        return ToolResult.fail(f"Could not find readable text on {url}. Try another result.")

    text = truncate_middle(text, get_settings().max_tool_output_chars)
    header = f"{title}\n{resp.url}\n\n" if title else f"{resp.url}\n\n"
    return ToolResult.success(header + text)
