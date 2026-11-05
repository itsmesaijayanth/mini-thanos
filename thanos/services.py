"""App-wide services tools can use without knowing about the CLI.

`notify(text)` alerts the user (timers, alarms). The CLI installs a handler that speaks the
text, shows a macOS notification and plays a chime; by default it only logs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

NotifyFn = Callable[[str], None]


def _log_only(text: str) -> None:
    log.warning("NOTIFY: %s", text)


_notify: NotifyFn = _log_only


def set_notifier(fn: NotifyFn | None) -> None:
    global _notify
    _notify = fn or _log_only


def notify(text: str) -> None:
    try:
        _notify(text)
    except Exception:
        log.warning("notify failed", exc_info=True)


# --- confirmation -------------------------------------------------------------
# Tools that act on the outside world (sending messages) ask the user first via `confirm`.
# The CLI installs a y/N prompt; without one, the answer is always "no" (safe default).

ConfirmFn = Callable[[str], bool]
_confirm: ConfirmFn | None = None


def set_confirmer(fn: ConfirmFn | None) -> None:
    global _confirm
    _confirm = fn


def confirm(prompt: str) -> bool:
    if _confirm is None:
        return False
    try:
        return bool(_confirm(prompt))
    except Exception:
        log.warning("confirm failed", exc_info=True)
        return False
