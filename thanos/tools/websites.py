"""Web shortcuts: open a site by name, Google something, Maps directions."""

from __future__ import annotations

import re
import webbrowser
from urllib.parse import quote_plus

from thanos.tools.base import ToolResult
from thanos.tools.registry import tool

KNOWN_SITES = {
    "gmail": "https://mail.google.com", "google": "https://www.google.com",
    "youtube": "https://www.youtube.com", "github": "https://github.com",
    "linkedin": "https://www.linkedin.com", "twitter": "https://x.com", "x": "https://x.com",
    "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com",
    "whatsapp": "https://web.whatsapp.com", "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai", "amazon": "https://www.amazon.in", "flipkart": "https://www.flipkart.com",
    "netflix": "https://www.netflix.com", "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com", "google calendar": "https://calendar.google.com",
    "maps": "https://maps.google.com", "google maps": "https://maps.google.com",
    "reddit": "https://www.reddit.com", "stack overflow": "https://stackoverflow.com",
    "wikipedia": "https://www.wikipedia.org", "spotify": "https://open.spotify.com",
}


def _open(url: str) -> bool:
    return webbrowser.open(url, new=2)


def site_url(name: str) -> str:
    n = name.strip().lower().removeprefix("the ").removesuffix(" website").removesuffix(" site").strip()
    if re.match(r"^https?://", n):
        return name.strip()
    if n in KNOWN_SITES:
        return KNOWN_SITES[n]
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", n):  # looks like a domain: example.com
        return "https://" + n
    return f"https://duckduckgo.com/?q={quote_plus('!ducky ' + name.strip())}"  # first result


@tool(
    category="web",
    direct=True,
    examples=["open github", "go to gmail", "open amazon website", "open example.com"],
)
def open_website(name: str) -> ToolResult:
    """Open a website in the browser by name or address.

    Args:
        name: Site name or URL, e.g. "github", "gmail", "nytimes.com".
    """
    url = site_url(name)
    if not _open(url):
        return ToolResult.fail("Couldn't open the browser.")
    return ToolResult.success(f"Opening {name}.", url=url)


@tool(
    category="web",
    direct=True,
    examples=["google best pizza near me", "search google for flights to Goa", "look up python dataclasses on google"],
)
def google_search(query: str) -> ToolResult:
    """Open a Google search for the query in the browser (for the user to look at).
    To read results yourself and answer, use web_search instead.

    Args:
        query: What to search, e.g. "best pizza near me".
    """
    url = f"https://www.google.com/search?q={quote_plus(query)}"
    if not _open(url):
        return ToolResult.fail("Couldn't open the browser.")
    return ToolResult.success(f"Searching Google for {query}.", url=url)


@tool(
    category="web",
    direct=True,
    examples=["directions to the airport", "how do I get to MG Road", "navigate to Phoenix mall from home"],
)
def maps_directions(destination: str, origin: str = "") -> ToolResult:
    """Open Google Maps with directions to a place.

    Args:
        destination: Where to go, e.g. "Bangalore airport".
        origin: Starting point, optional; empty = current location.
    """
    url = f"https://www.google.com/maps/dir/?api=1&destination={quote_plus(destination)}"
    if origin.strip():
        url += f"&origin={quote_plus(origin)}"
    if not _open(url):
        return ToolResult.fail("Couldn't open the browser.")
    return ToolResult.success(f"Opening directions to {destination}.", url=url)
