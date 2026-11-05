from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from thanos.tools import files


def test_find_files_formats(monkeypatch, tmp_path) -> None:
    a = tmp_path / "resume.pdf"
    a.write_text("x")
    monkeypatch.setattr(files, "spotlight", lambda q, kind="any": [a])
    r = files.find_files("resume")
    assert r.ok and "1. resume.pdf" in r.message and r.data["paths"] == [str(a)]
    monkeypatch.setattr(files, "spotlight", lambda q, kind="any": [])
    assert "No pdf files" in files.find_files("zzz", "pdf").message


def test_open_path(monkeypatch, tmp_path) -> None:
    opened = []
    monkeypatch.setattr(files.subprocess, "run", lambda cmd, **k: opened.append(cmd))
    assert files.open_path(str(tmp_path)).ok and opened[-1] == ["open", str(tmp_path)]
    assert files.open_path("downloads").ok and opened[-1] == ["open", str(Path.home() / "Downloads")]
    assert not files.open_path("/no/such/thing").ok


def test_page_range() -> None:
    assert files._page_range("", 3) == [0, 1, 2]
    assert files._page_range("1-2,5", 10) == [0, 1, 4]
    assert files._page_range("9", 3) == []


def test_read_pdf_blank_and_missing(monkeypatch, tmp_path) -> None:
    pdf = tmp_path / "blank.pdf"
    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.write(pdf)
    r = files.read_pdf(str(pdf))
    assert not r.ok and "no selectable text" in r.message
    monkeypatch.setattr(files, "spotlight", lambda q, kind="any": [])
    assert not files.read_pdf("nothing-here.pdf").ok


def test_spotlight_filters_code_and_ranks_documents(monkeypatch, tmp_path) -> None:
    import subprocess

    old = tmp_path / "resume.pdf"
    code = tmp_path / "src" / "test_resume.py"
    venv = tmp_path / ".venv" / "resume.txt"
    newer = tmp_path / "resume notes.zip"
    for p in (old, code, venv, newer):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    out = "\n".join(map(str, [code, venv, newer, old]))
    monkeypatch.setattr(files.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))
    assert files.spotlight("resume") == [old, newer]
    assert code in files.spotlight("resume.py")
