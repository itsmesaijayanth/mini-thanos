"""Terminal tools: run any shell command, and write files without shell-quoting pain.

Commands run without confirmation (user's choice). BLOCKED_PATTERNS is the hard safety net
for commands that could wreck the machine; extend it as needed.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from pathlib import Path

from thanos.config import get_settings
from thanos.tools.base import ToolResult, truncate_middle
from thanos.tools.registry import tool

# (pattern, reason). Matched case-insensitively against the whole command string.
BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r"\bsudo\b", "sudo (root access)"),
    (
        r"\brm\b[^;&|]*\s-[a-z]*r[a-z]*\b[^;&|]*\s(/|/\*|~/?|~/\*|\$HOME/?)(\s|;|&|\||$)",
        "recursive delete of / or home",
    ),
    (r"\bmkfs(\.\w+)?\b", "formatting a disk"),
    (r"\bdiskutil\s+(erase\w*|zero\w*|secureerase|partitiondisk)\b", "erasing a disk"),
    (r"\bdd\b.*\bof=/dev/", "writing raw to a device"),
    (r">\s*/dev/(r?disk|sd)", "writing raw to a device"),
    (r"\b(shutdown|reboot|halt)\b", "shutting down the machine"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
    (r"\b(curl|wget)\b.*\|\s*(ba|z)?sh\b", "piping a download into a shell"),
    (r"\b(chmod|chown)\s+-r\b.*\s/(\s|$)", "changing permissions on /"),
]
_BLOCKED = [(re.compile(p, re.IGNORECASE), why) for p, why in BLOCKED_PATTERNS]


def blocked_reason(command: str) -> str | None:
    for pattern, why in _BLOCKED:
        if pattern.search(command):
            return why
    return None


@tool(
    category="system",
    examples=["list files in my downloads folder", "how much disk space is free", "what's in notes.txt",
              "run the command ls", "check my ip address"],
)
def run_shell(command: str, timeout_seconds: int = 30) -> ToolResult:
    """Run a bash command on the user's Mac and return its output.
    Use it to list folders (ls), read text files like "what's in notes.txt" (cat), check disk
    space (df -h), search,
    copy, move or delete files, and other system info. Starts in the workspace folder
    ~/Thanos; use absolute or ~ paths for other places (e.g. `ls ~/Downloads`).
    Do NOT use it to write long text into files — use write_file for that.

    Args:
        command: The bash command, e.g. "ls -la ~/Downloads" or "cat ~/Thanos/notes.txt".
        timeout_seconds: Seconds before the command is stopped. Default 30.
    """
    settings = get_settings()
    command = command.strip()
    if not command:
        return ToolResult.fail("command is empty.")
    if why := blocked_reason(command):
        return ToolResult.fail(f"Blocked for safety: {why}. Do not retry this command.")

    timeout = max(1, min(timeout_seconds, settings.shell_max_timeout))
    cwd = settings.workspace
    proc = subprocess.Popen(
        ["/bin/bash", "-c", command],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,  # own process group, so a timeout kills children too
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        return ToolResult.fail(
            f"Timed out after {timeout}s. Partial output:\n"
            + truncate_middle(output or "", settings.max_tool_output_chars),
            exit_code=None,
        )

    output = truncate_middle((output or "").rstrip(), settings.max_tool_output_chars)
    code = proc.returncode
    body = output if output else "(no output)"
    if code == 0:
        return ToolResult.success(body, exit_code=0)
    return ToolResult.fail(f"Exit code {code}.\n{body}", exit_code=code)


@tool(
    category="system",
    examples=["create a file todo.txt with buy milk", "save this text to a file", "write notes to ideas.txt"],
)
def write_file(path: str, content: str, append: bool = False) -> ToolResult:
    """Save text to a file exactly as given (creates folders if needed).
    Use this whenever the user wants something saved, written, or noted into a file.

    Args:
        path: File path. Relative paths go inside ~/Thanos, e.g. "notes/todo.txt".
            Use ~ or absolute paths for other places, e.g. "~/Desktop/list.txt".
        content: The full text to write.
        append: True to add to the end of an existing file instead of replacing it.
    """
    target = resolve_path(path)
    if target.exists() and target.is_dir():
        return ToolResult.fail(f"{target} is a folder, not a file.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    verb = "Appended" if append else "Saved"
    return ToolResult.success(f"{verb} {len(content)} characters to {target}.", path=str(target))


def resolve_path(path: str) -> Path:
    workspace = get_settings().workspace
    p = Path(path.strip()).expanduser()
    if not p.is_absolute():
        return (workspace / p).resolve()
    # Small models often assume Linux: /home/<user>/x -> ~/x, /home/Thanos/x -> workspace/x
    parts = p.parts
    if len(parts) >= 3 and parts[1] == "home":
        rest = Path(*parts[3:]) if len(parts) > 3 else Path()
        base = workspace if parts[2] == workspace.name else Path.home()
        return (base / rest).resolve()
    if len(parts) >= 2 and parts[1] == workspace.name:  # "/Thanos/x"
        return (workspace / Path(*parts[2:])).resolve()
    return p.resolve()
