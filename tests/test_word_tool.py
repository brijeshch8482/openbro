"""Tests for the Word document tool."""

import pytest

from openbro.tools.word_tool import WordTool

# Skip the whole file if python-docx isn't installed
docx = pytest.importorskip("docx")


@pytest.fixture
def sample_docx(tmp_path):
    """Create a sample .docx with known content."""
    doc = docx.Document()
    doc.add_paragraph("Introduction")
    doc.add_paragraph("This is the first paragraph about cats.")
    doc.add_paragraph("Conclusion")
    doc.add_paragraph("Final remarks here.")
    p = tmp_path / "sample.docx"
    doc.save(str(p))
    return p


def test_schema_shape():
    schema = WordTool().schema()
    assert schema["name"] == "word"
    actions = schema["parameters"]["properties"]["action"]["enum"]
    for a in ("open", "read", "info", "find_replace", "append", "insert_after", "list"):
        assert a in actions


def test_run_unknown_action():
    out = WordTool().run(action="bogus", file="x.docx")
    assert "Unknown action" in out


def test_list_finds_docx_files(tmp_path, sample_docx):
    out = WordTool().run(action="list", folder=str(tmp_path))
    assert "sample.docx" in out


def test_list_empty_folder(tmp_path):
    out = WordTool().run(action="list", folder=str(tmp_path))
    assert "No .docx files" in out


def test_list_nonexistent_folder():
    out = WordTool().run(action="list", folder="/nonexistent/path/xyz")
    assert "not found" in out.lower()


def test_read_extracts_text(sample_docx):
    out = WordTool().run(action="read", file=str(sample_docx))
    assert "Introduction" in out
    assert "first paragraph about cats" in out
    assert "Conclusion" in out


def test_read_missing_file(tmp_path):
    out = WordTool().run(action="read", file=str(tmp_path / "missing.docx"))
    assert "not found" in out.lower()


def test_read_rejects_non_docx(tmp_path):
    bad = tmp_path / "x.txt"
    bad.write_text("hello")
    out = WordTool().run(action="read", file=str(bad))
    assert "Only .docx" in out


def test_read_with_limit(sample_docx):
    out = WordTool().run(action="read", file=str(sample_docx), limit=20)
    assert "more chars" in out  # truncation marker present


def test_info_returns_counts(sample_docx):
    out = WordTool().run(action="info", file=str(sample_docx))
    assert "Paragraphs: 4" in out
    assert "Words:" in out
    assert "Tables: 0" in out


def test_find_replace_makes_edit(sample_docx):
    out = WordTool().run(
        action="find_replace",
        file=str(sample_docx),
        find="cats",
        replace="dogs",
    )
    assert "Replaced 1" in out
    # Verify on disk
    text = WordTool().run(action="read", file=str(sample_docx))
    assert "dogs" in text
    assert "cats" not in text


def test_find_replace_no_match(sample_docx):
    out = WordTool().run(
        action="find_replace",
        file=str(sample_docx),
        find="elephants",
        replace="dogs",
    )
    assert "not found" in out.lower()


def test_find_replace_save_as_keeps_original(sample_docx, tmp_path):
    copy_path = tmp_path / "edited.docx"
    out = WordTool().run(
        action="find_replace",
        file=str(sample_docx),
        find="cats",
        replace="dogs",
        save_as=str(copy_path),
    )
    assert "Replaced" in out
    # Original untouched
    orig_text = WordTool().run(action="read", file=str(sample_docx))
    assert "cats" in orig_text
    # Copy edited
    new_text = WordTool().run(action="read", file=str(copy_path))
    assert "dogs" in new_text


def test_find_replace_requires_find(sample_docx):
    out = WordTool().run(action="find_replace", file=str(sample_docx), find="", replace="x")
    assert "required" in out.lower()


def test_append_adds_paragraph(sample_docx):
    out = WordTool().run(action="append", file=str(sample_docx), text="Appended text!")
    assert "Saved" in out
    text = WordTool().run(action="read", file=str(sample_docx))
    assert "Appended text!" in text


def test_append_requires_text(sample_docx):
    out = WordTool().run(action="append", file=str(sample_docx), text="")
    assert "required" in out.lower()


def test_insert_after_works(sample_docx):
    out = WordTool().run(
        action="insert_after",
        file=str(sample_docx),
        after="Conclusion",
        text="A summary paragraph.",
    )
    assert "Inserted" in out
    text = WordTool().run(action="read", file=str(sample_docx))
    # The new paragraph should appear after "Conclusion"
    lines = [line for line in text.splitlines() if line.strip()]
    concl_idx = next(i for i, line in enumerate(lines) if "Conclusion" in line)
    assert "A summary paragraph." in lines[concl_idx + 1]


def test_insert_after_marker_not_found(sample_docx):
    out = WordTool().run(
        action="insert_after",
        file=str(sample_docx),
        after="NonExistentMarker",
        text="x",
    )
    assert "not found" in out.lower()


def test_insert_after_requires_both(sample_docx):
    out = WordTool().run(action="insert_after", file=str(sample_docx), after="", text="x")
    assert "required" in out.lower()
