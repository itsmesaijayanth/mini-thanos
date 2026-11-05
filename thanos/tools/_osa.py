"""Shared helpers for macOS tools: run AppleScript / commands with friendly errors."""

from __future__ import annotations

import subprocess
from datetime import datetime

PERMISSION_HELP = (
    "macOS blocked this. Allow it in System Settings → Privacy & Security → {pane} "
    "for the app running Thanos (Terminal / VS Code), then try again."
)


class OSAError(RuntimeError):
    pass


def run_applescript(script: str, timeout: float = 15) -> str:
    """Run AppleScript and return its stdout (stripped). Raises OSAError with a clear message."""
    try:
        proc = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise OSAError(
            "The app didn't respond. If macOS is showing a permission pop-up, click Allow and try again."
        ) from e
    if proc.returncode != 0:
        raise OSAError(explain_osa_error(proc.stderr))
    return proc.stdout.strip()


def explain_osa_error(stderr: str) -> str:
    err = (stderr or "").strip()
    if "-1743" in err or "not authorized" in err.lower() or "not allowed to send" in err.lower():
        return PERMISSION_HELP.format(pane="Automation")
    if "-1719" in err or "assistive access" in err.lower() or "not allowed assistive" in err.lower():
        return PERMISSION_HELP.format(pane="Accessibility")
    if "-1728" in err:
        return "Not found. " + err.split("execution error:")[-1].strip()
    return err.split("execution error:")[-1].strip() or "AppleScript failed."


def q(text: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def run(cmd: list[str], timeout: float = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def applescript_date(var: str, dt: datetime) -> str:
    """AppleScript lines that build date `var` from parts (locale-proof, unlike date "...")."""
    return (
        f"set {var} to current date\n"
        f"set day of {var} to 1\n"
        f"set year of {var} to {dt.year}\n"
        f"set month of {var} to {dt.month}\n"
        f"set day of {var} to {dt.day}\n"
        f"set time of {var} to {dt.hour * 3600 + dt.minute * 60 + dt.second}\n"
    )
