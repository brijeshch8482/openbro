"""Tests for the universal DocumentTool — extension dispatch + graceful
fallbacks when optional deps are missing.

We avoid bundling binary fixtures (PDFs/images/audio): instead we test the
dispatch logic, the deps-missing messages, and the formats that are
covered by stdlib (CSV, JSON, YAML, zip, text)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from openbro.tools.document_tool import (
    AUDIO_EXTS,
    DOCX_EXTS,
    EXCEL_EXTS,
    HTML_EXTS,
    IMAGE_EXTS,
    PDF_EXTS,
    ZIP_EXTS,
    DocumentTool,
)


def test_unknown_action(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    out = DocumentTool().run(action="delete", file=str(f))
    assert "Unknown action" in out


def test_file_required():
    tool = DocumentTool()
    out = tool.run(action="read")
    assert "'file' is required" in out


def test_missing_file(tmp_path):
    tool = DocumentTool()
    out = tool.run(action="read", file=str(tmp_path / "nope.pdf"))
    assert "not found" in out.lower()


def test_directory_rejected(tmp_path):
    tool = DocumentTool()
    out = tool.run(action="read", file=str(tmp_path))
    assert "directory" in out.lower()


def test_info_reports_backend(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4 placeholder")
    out = DocumentTool().run(action="info", file=str(f))
    assert "Backend: pypdf" in out
    assert "x.pdf" in out


def test_read_text_file(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello bro\nline two", encoding="utf-8")
    out = DocumentTool().run(action="read", file=str(f))
    assert "hello bro" in out
    assert "line two" in out


def test_read_python_file_works_via_text_fallback(tmp_path):
    f = tmp_path / "script.py"
    f.write_text("print('hi')\n", encoding="utf-8")
    out = DocumentTool().run(action="read", file=str(f))
    assert "print('hi')" in out


def test_read_json_pretty_prints(tmp_path):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"name": "openbro", "version": 1}), encoding="utf-8")
    out = DocumentTool().run(action="read", file=str(f))
    assert '"name": "openbro"' in out


def test_read_malformed_json_returns_raw(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    out = DocumentTool().run(action="read", file=str(f))
    assert "not json" in out


def test_read_yaml_roundtrips(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("name: openbro\nversion: 1\n", encoding="utf-8")
    out = DocumentTool().run(action="read", file=str(f))
    assert "name: openbro" in out


def test_read_csv_summarizes(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,age\nA,1\nB,2\n", encoding="utf-8")
    out = DocumentTool().run(action="read", file=str(f))
    assert "Rows: 3" in out
    assert "Cols: 2" in out
    assert "name\tage" in out


def test_read_tsv_uses_tab_delimiter(tmp_path):
    f = tmp_path / "data.tsv"
    f.write_text("a\tb\n1\t2\n", encoding="utf-8")
    out = DocumentTool().run(action="read", file=str(f))
    assert "Rows: 2" in out


def test_read_zip_lists_entries(tmp_path):
    z = tmp_path / "bundle.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "hello")
        zf.writestr("nested/b.txt", "world")
    out = DocumentTool().run(action="read", file=str(z))
    assert "a.txt" in out
    assert "nested/b.txt" in out
    assert "2 entries" in out


def test_read_invalid_zip(tmp_path):
    z = tmp_path / "not.zip"
    z.write_text("nope")
    out = DocumentTool().run(action="read", file=str(z))
    assert "Not a valid zip" in out


def test_pdf_without_pypdf_returns_install_hint(tmp_path, monkeypatch):
    """When pypdf is missing we point the user at the install command instead
    of crashing or silently returning empty."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF placeholder")
    out = DocumentTool().run(action="read", file=str(f))
    assert "pypdf" in out
    assert "install" in out.lower()


def test_image_without_pytesseract_returns_install_hint(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("pytesseract", "PIL"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    f = tmp_path / "shot.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n")
    out = DocumentTool().run(action="read", file=str(f))
    assert "pytesseract" in out


def test_html_without_bs4_returns_install_hint(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("bs4", "beautifulsoup4"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    f = tmp_path / "page.html"
    f.write_text("<html><body><h1>hi</h1></body></html>", encoding="utf-8")
    out = DocumentTool().run(action="read", file=str(f))
    assert "beautifulsoup4" in out


def test_extension_sets_cover_expected_formats():
    """Sanity check: file_tool delegates to these sets — if a format is
    accidentally removed, file_ops read would silently revert to garbage
    text reads. Pin the contracts."""
    assert ".pdf" in PDF_EXTS
    assert ".docx" in DOCX_EXTS
    assert ".xlsx" in EXCEL_EXTS
    assert ".png" in IMAGE_EXTS and ".jpg" in IMAGE_EXTS
    assert ".mp3" in AUDIO_EXTS and ".wav" in AUDIO_EXTS
    assert ".html" in HTML_EXTS
    assert ".zip" in ZIP_EXTS


def test_schema_advertises_read_and_info():
    schema = DocumentTool().schema()
    enum = schema["parameters"]["properties"]["action"]["enum"]
    assert "read" in enum
    assert "info" in enum
    assert "file" in schema["parameters"]["required"]


def test_docx_dispatch_when_python_docx_missing(tmp_path, monkeypatch):
    """When python-docx is missing, .docx read returns the install hint
    rather than crashing — the user can still understand what to do."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "docx":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    f = tmp_path / "x.docx"
    f.write_bytes(b"PK\x03\x04 placeholder")
    out = DocumentTool().run(action="read", file=str(f))
    assert "python-docx" in out


def test_excel_dispatch_when_openpyxl_missing(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    f = tmp_path / "x.xlsx"
    f.write_bytes(b"PK\x03\x04 placeholder")
    out = DocumentTool().run(action="read", file=str(f))
    assert "openpyxl" in out


def test_audio_without_faster_whisper_returns_install_hint(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    f = tmp_path / "clip.mp3"
    f.write_bytes(b"ID3\x03 placeholder")
    out = DocumentTool().run(action="read", file=str(f))
    assert "faster-whisper" in out or "openbro[voice]" in out


def test_document_tool_registered_in_registry(tmp_path: Path):
    from openbro.tools.registry import ToolRegistry

    reg = ToolRegistry()
    assert reg.get_tool("document") is not None
    schema_names = [s["name"] for s in reg.get_tools_schema()]
    assert "document" in schema_names


@pytest.mark.parametrize(
    "ext,expected_backend_kw",
    [
        (".pdf", "pypdf"),
        (".docx", "python-docx"),
        (".xlsx", "openpyxl"),
        (".csv", "csv"),
        (".json", "json"),
        (".yaml", "PyYAML"),
        (".html", "beautifulsoup4"),
        (".png", "pytesseract"),
        (".mp3", "faster-whisper"),
        (".zip", "zipfile"),
    ],
)
def test_backend_dispatch_label(ext, expected_backend_kw):
    assert expected_backend_kw in DocumentTool._backend_for(ext)
