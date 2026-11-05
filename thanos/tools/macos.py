"""Mac controls: volume, brightness, apps, lock/sleep, dark mode, battery, screenshots."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from thanos.tools._osa import OSAError, q, run, run_applescript
from thanos.tools.base import ToolResult
from thanos.tools.registry import tool

VOLUME_STEP = 10
LOW_BATTERY = 10  # percent; below this the (unreliable) time estimate is skipped
BRIGHTNESS_KEYS = {"up": 144, "down": 145}


def _get_volume() -> int:
    return int(run_applescript("output volume of (get volume settings)"))


@tool(
    category="system",
    direct=True,
    examples=["set volume to 30", "volume 50 percent", "make the volume 80"],
)
def set_volume(level: int) -> ToolResult:
    """Set the Mac's sound volume to an exact level from 0 to 100.

    Args:
        level: Volume percent, e.g. 30.
    """
    level = max(0, min(100, level))
    try:
        run_applescript(f"set volume output volume {level} without output muted")
    except OSAError as e:
        return ToolResult.fail(str(e))
    return ToolResult.success(f"Volume set to {level} percent.")


@tool(
    category="system",
    direct=True,
    examples=["turn it up", "louder", "volume down", "it's too loud", "decrease the volume"],
)
def change_volume(direction: Literal["up", "down"]) -> ToolResult:
    """Turn the volume up or down a step (10%). Use for "louder", "quieter", "turn it up",
    "it's too quiet" (up) or "it's too loud" (down). Call it right away; don't just reply.

    Args:
        direction: "up" or "down".
    """
    try:
        current = _get_volume()
        new = max(0, min(100, current + (VOLUME_STEP if direction == "up" else -VOLUME_STEP)))
        run_applescript(f"set volume output volume {new} without output muted")
    except (OSAError, ValueError) as e:
        return ToolResult.fail(str(e))
    return ToolResult.success(f"Volume {direction} to {new} percent.")


@tool(category="system", direct=True, examples=["mute", "unmute the sound", "mute my mac"])
def mute(on: bool = True) -> ToolResult:
    """Mute or unmute the Mac's sound.

    Args:
        on: True to mute, False to unmute.
    """
    try:
        run_applescript(f"set volume output muted {'true' if on else 'false'}")
    except OSAError as e:
        return ToolResult.fail(str(e))
    return ToolResult.success("Muted." if on else "Unmuted.")


@tool(
    category="system",
    direct=True,
    examples=["increase brightness", "make the screen dimmer", "brightness down", "screen brighter"],
)
def change_brightness(direction: Literal["up", "down"], steps: int = 2) -> ToolResult:
    """Make the screen brighter or dimmer.

    Args:
        direction: "up" (brighter) or "down" (dimmer).
        steps: How many brightness steps (1-16). Default 2.
    """
    steps = max(1, min(16, steps))
    code = BRIGHTNESS_KEYS[direction]
    script = (
        f'tell application "System Events"\n'
        f"repeat {steps} times\nkey code {code}\ndelay 0.05\nend repeat\nend tell"
    )
    try:
        run_applescript(script)
    except OSAError as e:
        return ToolResult.fail(str(e))
    return ToolResult.success("Brighter." if direction == "up" else "Dimmer.")


@tool(
    category="system",
    direct=True,
    examples=["open safari", "launch slack", "start whatsapp", "open calculator app"],
)
def open_app(name: str) -> ToolResult:
    """Open (launch or bring to front) a Mac application by name.

    Args:
        name: App name as in the Applications folder, e.g. "Safari", "Slack", "Calculator".
    """
    proc = run(["open", "-a", name.strip()])
    if proc.returncode != 0:
        return ToolResult.fail(f"Couldn't find an app called {name}.")
    return ToolResult.success(f"Opened {name}.")


@tool(category="system", direct=True, examples=["quit slack", "close safari", "kill whatsapp"])
def quit_app(name: str) -> ToolResult:
    """Quit a running Mac application by name.

    Args:
        name: App name, e.g. "Slack".
    """
    try:
        running = run_applescript(f"application {q(name)} is running")
        if running != "true":
            return ToolResult.success(f"{name} isn't running.")
        run_applescript(f"tell application {q(name)} to quit")
    except OSAError as e:
        return ToolResult.fail(str(e))
    return ToolResult.success(f"Quit {name}.")


@tool(category="system", direct=True, examples=["lock my screen", "lock the mac", "lock computer"])
def lock_screen() -> ToolResult:
    """Lock the Mac (shows the login screen)."""
    try:
        run_applescript(
            'tell application "System Events" to keystroke "q" using {control down, command down}'
        )
    except OSAError:
        run(["pmset", "displaysleepnow"])  # locks too when a password is required after sleep
    return ToolResult.success("Locked.")


@tool(category="system", direct=True, examples=["turn off the screen", "sleep the display"])
def sleep_display() -> ToolResult:
    """Turn the display off (sleep the screen) without sleeping the Mac."""
    run(["pmset", "displaysleepnow"])
    return ToolResult.success("Display off.")


@tool(
    category="system",
    direct=True,
    examples=["turn on dark mode", "switch to light mode", "disable dark mode"],
)
def set_dark_mode(on: bool) -> ToolResult:
    """Turn macOS dark mode on or off.

    Args:
        on: True for dark mode, False for light mode.
    """
    try:
        run_applescript(
            'tell application "System Events" to tell appearance preferences to '
            f"set dark mode to {'true' if on else 'false'}"
        )
    except OSAError as e:
        return ToolResult.fail(str(e))
    return ToolResult.success("Dark mode on." if on else "Light mode on.")


_BATT = re.compile(r"(\d+)%;\s*([^;]+);\s*([^\s]+)?")


@tool(
    category="system",
    direct=True,
    examples=["how much battery is left", "battery percentage", "is my laptop charging"],
)
def battery_status() -> ToolResult:
    """Get the battery level, whether it's charging, and time remaining."""
    out = run(["pmset", "-g", "batt"]).stdout
    return parse_battery(out)


def parse_battery(out: str) -> ToolResult:
    m = _BATT.search(out)
    if not m:
        return ToolResult.fail("No battery found.")
    pct, state, remaining = int(m.group(1)), m.group(2).strip(), m.group(3) or ""
    discharging = state not in ("charging", "charged", "finishing charge", "AC attached")
    if pct <= LOW_BATTERY and discharging:
        return ToolResult.success(f"Battery is low, at {pct} percent. Plug in soon.", percent=pct, state=state)
    msg = f"Battery is at {pct} percent"
    if state in ("charging", "charged", "finishing charge"):
        msg += ", " + ("fully charged" if state == "charged" else "charging")
    elif state == "AC attached":
        msg += ", plugged in"
    if re.fullmatch(r"\d+:\d\d", remaining) and remaining != "0:00":
        h, mnt = remaining.split(":")
        when = f"{int(h)} hours {int(mnt)} minutes" if int(h) else f"{int(mnt)} minutes"
        msg += f", about {when} " + ("to full" if state == "charging" else "left")
    return ToolResult.success(msg + ".", percent=pct, state=state)


@tool(
    category="system",
    direct=True,
    examples=["take a screenshot", "screenshot this window", "capture my screen"],
)
def take_screenshot(area: Literal["screen", "window", "selection"] = "screen") -> ToolResult:
    """Take a screenshot and save it to the Desktop.

    Args:
        area: "screen" (whole screen, default), "window" (click a window) or "selection" (drag an area).
    """
    path = Path("~/Desktop").expanduser() / datetime.now().strftime("Screenshot %Y-%m-%d at %H.%M.%S.png")
    flags = {"screen": ["-x"], "window": ["-x", "-w"], "selection": ["-x", "-i"]}[area]
    proc = run(["screencapture", *flags, str(path)], timeout=60)
    if proc.returncode != 0 or not path.exists():
        return ToolResult.fail("Screenshot cancelled or failed.")
    return ToolResult.success("Screenshot saved to your Desktop.", path=str(path))
