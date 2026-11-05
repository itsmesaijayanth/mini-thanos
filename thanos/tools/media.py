"""Music and media: Apple Music (your library) and play/pause for a YouTube tab."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from thanos.tools._osa import OSAError, q, run_applescript
from thanos.tools.base import ToolResult
from thanos.tools.registry import tool

_SEARCH_KIND = {"song": "songs", "artist": "artists", "album": "albums"}


@tool(
    category="media",
    direct=True,
    examples=["play some Arijit Singh in Music", "play my workout playlist", "play the song Kesariya",
              "play music"],
)
def play_music(query: str = "", kind: Literal["song", "artist", "album", "playlist"] = "song") -> ToolResult:
    """Play from the user's Apple Music library. ONLY when they mention Apple Music, the Music app,
    or "my library"/"my playlist". For any other "play <song/artist>" use play_youtube.

    Args:
        query: Song, artist, album or playlist name, e.g. "Kesariya". Empty = resume/shuffle music.
        kind: What the query is: "song", "artist", "album" or "playlist". Default "song".
    """
    if not query.strip():
        return music_control("play")
    if kind == "playlist":
        script = f'''tell application "Music"
set ps to (every user playlist whose name contains {q(query)})
if (count of ps) = 0 then return "NOTFOUND"
play item 1 of ps
return name of item 1 of ps
end tell'''
    else:
        script = f'''tell application "Music"
set hits to (search library playlist 1 for {q(query)} only {_SEARCH_KIND[kind]})
if (count of hits) = 0 then return "NOTFOUND"
play item 1 of hits
return (name of item 1 of hits) & " by " & (artist of item 1 of hits)
end tell'''
    try:
        out = run_applescript(script, timeout=20)
    except OSAError as e:
        return ToolResult.fail(str(e))
    if out == "NOTFOUND":
        return ToolResult.fail(
            f"'{query}' isn't in your Music library. Offer to play it on YouTube instead."
        )
    return ToolResult.success(f"Playing {out}.")


@tool(
    category="media",
    direct=True,
    examples=["pause the music", "next song", "skip this track", "previous song", "resume music"],
)
def music_control(action: Literal["play", "pause", "next", "previous", "stop"]) -> ToolResult:
    """Control the Music app: play/resume, pause, next track, previous track, stop.

    Args:
        action: "play", "pause", "next", "previous" or "stop".
    """
    cmd = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track",
           "stop": "stop"}[action]
    # pause/next/previous/stop shouldn't launch Music just to do nothing
    guard = '' if action == "play" else 'if application "Music" is not running then return "OFF"\n'
    try:
        out = run_applescript(f'{guard}tell application "Music" to {cmd}')
        if out == "OFF":
            return ToolResult.fail("Music isn't open. If a YouTube video is playing, use youtube_control.")
    except OSAError as e:
        return ToolResult.fail(str(e))
    return ToolResult.success({"play": "Playing.", "pause": "Paused.", "next": "Next track.",
                               "previous": "Previous track.", "stop": "Stopped."}[action])


@tool(category="media", direct=True, examples=["what song is this", "what's playing right now"])
def now_playing() -> ToolResult:
    """Say which song is playing in the Music app."""
    script = '''if application "Music" is not running then return "NOTHING"
tell application "Music"
if player state is not playing then return "NOTHING"
return (name of current track) & " by " & (artist of current track)
end tell'''
    try:
        out = run_applescript(script)
    except OSAError as e:
        return ToolResult.fail(str(e))
    if out == "NOTHING":
        return ToolResult.success("Nothing is playing in Music right now.")
    return ToolResult.success(f"This is {out}.")


_JS = {
    "play": "v.play()",
    "pause": "v.pause()",
    "toggle": "(v.paused ? v.play() : v.pause())",
}
_JS_WRAP = (
    "(function(){{var v=document.querySelector('video');if(!v)return 'novideo';{op};"
    "return v.paused?'paused':'playing';}})()"
)
JS_HELP = (
    "Allow JavaScript from Apple Events once — Brave: View → Developer → "
    "Allow JavaScript from Apple Events; Safari: Settings → Advanced → Show features for web "
    "developers, then Develop → Allow JavaScript from Apple Events."
)


def _browser_script(browser: str, js: str) -> str:
    if browser == "Safari":
        run_js = f"do JavaScript {q(js)} in t"
    else:
        run_js = f"execute t javascript {q(js)}"
    return f'''if application "{browser}" is running then
tell application "{browser}"
repeat with w in windows
repeat with t in tabs of w
if URL of t contains "youtube.com/watch" then return {run_js}
end repeat
end repeat
end tell
end if
return "NOTAB"'''


def installed_browsers() -> list[str]:
    # Only installed apps: naming a missing app in AppleScript pops up a "Where is …?" dialog.
    names = ("Brave Browser", "Safari", "Google Chrome")
    return [n for n in names
            if any(Path(d, f"{n}.app").exists() for d in ("/Applications", "/System/Applications"))]


@tool(
    category="media",
    direct=True,
    examples=["pause the youtube video", "resume the video", "stop youtube", "pause it"],
)
def youtube_control(action: Literal["play", "pause", "toggle"]) -> ToolResult:
    """Play or pause the YouTube video open in the browser (Brave or Safari).

    Args:
        action: "play", "pause" or "toggle".
    """
    js = _JS_WRAP.format(op=_JS[action])
    last_error = ""
    for browser in installed_browsers():
        try:
            out = run_applescript(_browser_script(browser, js))
        except OSAError as e:
            last_error = str(e)
            if "javascript" in last_error.lower() or "apple events" in last_error.lower():
                return ToolResult.fail(JS_HELP)
            continue
        if out == "NOTAB":
            continue
        if out == "novideo":
            return ToolResult.fail("The YouTube tab has no video.")
        return ToolResult.success("Paused." if out == "paused" else "Playing.")
    return ToolResult.fail(last_error or "No YouTube video is open in the browser.")
