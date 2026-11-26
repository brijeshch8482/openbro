"""Harness piece 4 — grep, glob, read, edit."""

import os
import time

import pytest

from openbro.tools.search_tools import (
    EditTool,
    FileState,
    GlobTool,
    GrepTool,
    ReadTool,
)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def main():\n    print('hello')\n\n\ndef helper():\n    return 42\n", encoding="utf-8"
    )
    (tmp_path / "src" / "notes.txt").write_text(
        "TODO: fix fee report\nall good\n", encoding="utf-8"
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.py").write_text("def main(): pass\n", encoding="utf-8")
    (tmp_path / "image.bin").write_bytes(b"\x00\x01def main\x00")
    return tmp_path


@pytest.fixture
def no_rg(monkeypatch):
    monkeypatch.setattr("openbro.tools.search_tools.shutil.which", lambda name: None)


# ─── grep ─────────────────────────────────────────────────────────────


def test_grep_content_skips_vendor_and_binary(tree, no_rg):
    out = GrepTool().run(pattern=r"def main", path=str(tree))
    assert "app.py:1:def main():" in out
    assert "node_modules" not in out
    assert "image.bin" not in out


def test_grep_files_count_and_glob(tree, no_rg):
    assert (
        GrepTool()
        .run(pattern="def ", path=str(tree), output_mode="files")
        .strip()
        .endswith("app.py")
    )
    assert GrepTool().run(pattern="def ", path=str(tree), output_mode="count").endswith(":2")
    assert "No matches" in GrepTool().run(pattern="def ", path=str(tree), glob="*.txt")


def test_grep_ignore_case_and_context(tree, no_rg):
    out = GrepTool().run(pattern="todo", path=str(tree), ignore_case=True, context=1)
    assert "notes.txt:1:TODO: fix fee report" in out
    assert "notes.txt-2-all good" in out


def test_grep_bad_regex_and_missing_path(tree, no_rg):
    assert "Invalid regex" in GrepTool().run(pattern="(", path=str(tree))
    assert "Path not found" in GrepTool().run(pattern="x", path=str(tree / "nope"))


def test_grep_result_limit(tmp_path, no_rg):
    (tmp_path / "big.txt").write_text("hit\n" * 50, encoding="utf-8")
    out = GrepTool().run(pattern="hit", path=str(tmp_path), max_results=5)
    assert out.count("big.txt:") == 5 and "limit reached" in out


# ─── glob ─────────────────────────────────────────────────────────────


def test_glob_recursive_newest_first(tree):
    newer = tree / "src" / "new.py"
    newer.write_text("x = 1\n", encoding="utf-8")
    os.utime(newer, (time.time() + 10, time.time() + 10))
    lines = GlobTool().run(pattern="**/*.py", path=str(tree)).splitlines()
    assert lines[0].endswith("new.py")
    assert any(line.endswith("app.py") for line in lines)
    assert not any("node_modules" in line for line in lines)


def test_glob_no_match_and_bad_folder(tree):
    assert "No files match" in GlobTool().run(pattern="*.xlsx", path=str(tree))
    assert "Folder not found" in GlobTool().run(pattern="*", path=str(tree / "nope"))


# ─── read ─────────────────────────────────────────────────────────────


def test_read_numbers_and_ranges(tree):
    path = str(tree / "src" / "app.py")
    out = ReadTool(FileState()).run(path=path)
    assert out.splitlines()[0] == "1\tdef main():"
    part = ReadTool(FileState()).run(path=path, offset=5, limit=1)
    assert part.splitlines()[0] == "5\tdef helper():"
    assert "read again with offset=6" in part


def test_read_rejects_binary_and_missing(tree):
    assert "not a text file" in ReadTool(FileState()).run(path=str(tree / "image.bin"))
    assert "File not found" in ReadTool(FileState()).run(path=str(tree / "missing.txt"))


# ─── edit ─────────────────────────────────────────────────────────────


def test_edit_requires_read_first(tree):
    state = FileState()
    out = EditTool(state).run(path=str(tree / "src" / "app.py"), old_string="42", new_string="43")
    assert "before editing" in out


def test_edit_replaces_unique_string(tree):
    state = FileState()
    path = tree / "src" / "app.py"
    ReadTool(state).run(path=str(path))
    out = EditTool(state).run(path=str(path), old_string="return 42", new_string="return 43")
    assert "replaced 1 occurrence" in out
    assert "return 43" in path.read_text(encoding="utf-8")
    # still editable after its own write
    assert "replaced" in EditTool(state).run(path=str(path), old_string="43", new_string="44")


def test_edit_ambiguous_and_replace_all(tree):
    state = FileState()
    path = tree / "src" / "app.py"
    ReadTool(state).run(path=str(path))
    assert "appears 2 times" in EditTool(state).run(
        path=str(path), old_string="def ", new_string="async def "
    )
    out = EditTool(state).run(
        path=str(path), old_string="def ", new_string="async def ", replace_all=True
    )
    assert "replaced 2 occurrences" in out


def test_edit_rejects_stale_file(tree):
    state = FileState()
    path = tree / "src" / "app.py"
    ReadTool(state).run(path=str(path))
    path.write_text("changed elsewhere\n", encoding="utf-8")
    os.utime(path, (time.time() + 20, time.time() + 20))
    assert "changed since you read it" in EditTool(state).run(
        path=str(path), old_string="changed", new_string="x"
    )


def test_edit_not_found_and_noop(tree):
    state = FileState()
    path = tree / "src" / "app.py"
    ReadTool(state).run(path=str(path))
    assert "not found in the file" in EditTool(state).run(
        path=str(path), old_string="zzz", new_string="y"
    )
    assert "nothing to change" in EditTool(state).run(
        path=str(path), old_string="a", new_string="a"
    )


def test_edit_keeps_crlf_line_endings(tmp_path):
    state = FileState()
    path = tmp_path / "win.txt"
    path.write_bytes(b"line one\r\nline two\r\n")
    ReadTool(state).run(path=str(path))
    EditTool(state).run(path=str(path), old_string="line one\nline two", new_string="first\nsecond")
    assert path.read_bytes() == b"first\r\nsecond\r\n"


# ─── registry ─────────────────────────────────────────────────────────


def test_tools_registered_with_risk():
    from openbro.tools.registry import ToolRegistry

    reg = ToolRegistry()
    assert {"grep", "glob", "read", "edit"} <= set(reg.list_tools())
    assert reg.get_risk("grep") == "safe"
    assert reg.get_risk("edit") == "moderate"
