"""Files: Spotlight search, open, recent downloads, read PDFs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

from thanos.config import get_settings
from thanos.tools.base import ToolResult, truncate_middle
from thanos.tools.registry import tool

KIND_QUERY = {
    "any": "",
    "pdf": " && kMDItemContentType == 'com.adobe.pdf'",
    "image": " && kMDItemContentTypeTree == 'public.image'",
    "document": " && (kMDItemContentTypeTree == 'public.text' || kMDItemContentTypeTree == 'public.composite-content'"
                " || kMDItemContentTypeTree == 'com.microsoft.office.xlsx' || kMDItemContentTypeTree == 'org.openxmlformats.openxml')",
    "folder": " && kMDItemContentType == 'public.folder'",
}
MAX_RESULTS = 10
# Developer noise: "find my resume" shouldn't return test_resume_handler.py
SKIP_DIRS = {"node_modules", ".venv", "venv", "site-packages", "__pycache__", ".git", "dist", "build",
             "target", ".cache"}
CODE_EXTS = {".py", ".pyc", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rb", ".rs", ".c", ".h",
             ".cpp", ".cs", ".php", ".swift", ".kt", ".sh", ".lock", ".map", ".class", ".o"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".pages", ".txt", ".rtf", ".md", ".xlsx", ".xls", ".csv",
            ".numbers", ".ppt", ".pptx", ".key", ".jpg", ".jpeg", ".png", ".heic"}


def short(path: Path | str) -> str:
    return str(path).replace(str(Path.home()), "~", 1)


def spotlight(query: str, kind: str = "any") -> list[Path]:
    q = query.replace("'", "").replace("\\", "")
    expr = f"kMDItemDisplayName == '*{q}*'cd{KIND_QUERY.get(kind, '')}"
    proc = subprocess.run(
        ["mdfind", "-onlyin", str(Path.home()), expr], capture_output=True, text=True, timeout=20
    )
    wants_ext = "." in query  # "report.py" -> the user asked for a specific file type
    paths = []
    for line in proc.stdout.splitlines():
        p = Path(line)
        if not line or "/Library/" in line or "/." in line or SKIP_DIRS & set(p.parts):
            continue
        if not wants_ext and p.suffix.lower() in CODE_EXTS:
            continue
        if p.exists():
            paths.append(p)
    # documents first, then newest
    return sorted(paths, key=lambda p: (p.suffix.lower() in DOC_EXTS, p.stat().st_mtime), reverse=True)


@tool(
    category="files",
    examples=["find my resume", "where is the invoice pdf", "search for photos named passport",
              "find the project folder"],
)
def find_files(query: str, kind: Literal["any", "pdf", "image", "document", "folder"] = "any") -> ToolResult:
    """Search the whole Mac for files/folders by name (Spotlight), newest first.
    Not for listing what's inside a folder (use run_shell with ls).

    Args:
        query: Words in the file name, e.g. "resume".
        kind: "any", "pdf", "image", "document" or "folder". Default "any".
    """
    hits = spotlight(query, kind)[:MAX_RESULTS]
    if not hits:
        return ToolResult.success(f"No {'' if kind == 'any' else kind + ' '}files named like '{query}'.")
    lines = [f"{i}. {p.name} — {short(p.parent)}" for i, p in enumerate(hits, 1)]
    return ToolResult.success(
        f"Found {len(hits)}:\n" + "\n".join(lines), paths=[str(p) for p in hits]
    )


@tool(
    category="files",
    direct=True,
    examples=["open my downloads folder", "open the file report.pdf", "show the desktop folder"],
)
def open_path(path: str) -> ToolResult:
    """Open a file (in its default app) or a folder (in Finder). Use a full path from find_files.

    Args:
        path: Full or ~ path, e.g. "~/Downloads" or "~/Documents/resume.pdf".
    """
    p = Path(path.strip()).expanduser()
    if not p.exists():
        named = {"downloads": "Downloads", "desktop": "Desktop", "documents": "Documents",
                 "home": "", "pictures": "Pictures", "music": "Music", "movies": "Movies"}
        key = path.strip().lower().strip("~/ ")
        if key in named:
            p = Path.home() / named[key]
        else:
            return ToolResult.fail(f"{path} doesn't exist. Use find_files to get the exact path.")
    subprocess.run(["open", str(p)], check=False)
    return ToolResult.success(f"Opened {p.name or 'your home folder'}.")


@tool(
    category="files",
    direct=True,
    examples=["what did I download recently", "show my latest downloads", "last file I downloaded"],
)
def recent_downloads(count: int = 5) -> ToolResult:
    """List the most recently downloaded files.

    Args:
        count: How many, 1-20. Default 5.
    """
    folder = Path.home() / "Downloads"
    files = [p for p in folder.iterdir() if not p.name.startswith(".")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    files = files[: max(1, min(count, 20))]
    if not files:
        return ToolResult.success("Your Downloads folder is empty.")
    return ToolResult.success(
        "Your latest downloads: " + ", ".join(p.name for p in files) + ".",
        paths=[str(p) for p in files],
    )


def _resolve_pdf(path: str) -> Path | None:
    p = Path(path.strip()).expanduser()
    if p.is_file():
        return p
    for base in (Path.home() / "Downloads", Path.home() / "Desktop", Path.home() / "Documents"):
        if (base / path).is_file():
            return base / path
    hits = spotlight(Path(path).stem, "pdf")
    return hits[0] if hits else None


def _page_range(pages: str, total: int) -> list[int]:
    if not pages.strip():
        return list(range(total))
    chosen: list[int] = []
    for part in pages.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            chosen += range(int(a) - 1, min(int(b), total))
        elif part:
            chosen.append(int(part) - 1)
    return [i for i in chosen if 0 <= i < total]


@tool(
    category="files",
    examples=["read the pdf valid_lossrun", "summarize my resume pdf", "what does page 2 of the invoice say"],
)
def read_pdf(path: str, pages: str = "") -> ToolResult:
    """Extract the text of a PDF so you can read or summarize it.

    Args:
        path: PDF path or file name, e.g. "~/Downloads/report.pdf" or "report.pdf".
        pages: Optional pages, e.g. "1", "1-3", "2,5". Empty = whole document.
    """
    from pypdf import PdfReader

    pdf = _resolve_pdf(path)
    if pdf is None:
        return ToolResult.fail(f"Couldn't find a PDF named {path}. Try find_files.")
    try:
        reader = PdfReader(str(pdf))
        idx = _page_range(pages, len(reader.pages))
        text = "\n".join((reader.pages[i].extract_text() or "") for i in idx).strip()
    except Exception as e:
        return ToolResult.fail(f"Couldn't read {pdf.name}: {e}")
    if not text:
        return ToolResult.fail(f"{pdf.name} has no selectable text (it may be a scanned image).")
    header = f"{pdf.name} ({len(reader.pages)} pages)\n\n"
    return ToolResult.success(header + truncate_middle(text, get_settings().max_tool_output_chars))
