"""Tests for the Excel spreadsheet tool."""

import pytest

from openbro.tools.excel_tool import ExcelTool, _coerce_value

# Skip the whole file if openpyxl isn't installed
openpyxl = pytest.importorskip("openpyxl")


@pytest.fixture
def sample_xlsx(tmp_path):
    """Create a sample .xlsx with two sheets and known content."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "People"
    ws.append(["Name", "Age", "City"])
    ws.append(["Brijesh", 28, "Mumbai"])
    ws.append(["Riya", 24, "Delhi"])

    ws2 = wb.create_sheet("Numbers")
    ws2.append(["A", "B"])
    ws2.append([10, 20])

    p = tmp_path / "sample.xlsx"
    wb.save(str(p))
    wb.close()
    return p


# ─── helpers ─────────────────────────────────────────────────


def test_coerce_value_int():
    assert _coerce_value("42") == 42


def test_coerce_value_float():
    assert _coerce_value("3.14") == 3.14


def test_coerce_value_bool():
    assert _coerce_value("true") is True
    assert _coerce_value("False") is False


def test_coerce_value_string():
    assert _coerce_value("hello") == "hello"


# ─── schema / errors ─────────────────────────────────────────


def test_schema_shape():
    schema = ExcelTool().schema()
    assert schema["name"] == "excel"
    actions = schema["parameters"]["properties"]["action"]["enum"]
    for a in (
        "open",
        "read",
        "info",
        "sheets",
        "get_cell",
        "set_cell",
        "append_row",
        "find_replace",
        "list",
    ):
        assert a in actions


def test_unknown_action():
    out = ExcelTool().run(action="bogus", file="x.xlsx")
    assert "Unknown action" in out


def test_rejects_non_xlsx(tmp_path):
    bad = tmp_path / "x.txt"
    bad.write_text("hi")
    out = ExcelTool().run(action="read", file=str(bad))
    assert "Only .xlsx" in out


def test_missing_file(tmp_path):
    out = ExcelTool().run(action="read", file=str(tmp_path / "missing.xlsx"))
    assert "not found" in out.lower()


# ─── list ────────────────────────────────────────────────────


def test_list_finds_xlsx(tmp_path, sample_xlsx):
    out = ExcelTool().run(action="list", folder=str(tmp_path))
    assert "sample.xlsx" in out


def test_list_empty_folder(tmp_path):
    out = ExcelTool().run(action="list", folder=str(tmp_path))
    assert "No .xlsx" in out


# ─── read / info / sheets ───────────────────────────────────


def test_read_default_sheet(sample_xlsx):
    out = ExcelTool().run(action="read", file=str(sample_xlsx))
    assert "[People]" in out
    assert "Brijesh" in out
    assert "Mumbai" in out


def test_read_specific_sheet(sample_xlsx):
    out = ExcelTool().run(action="read", file=str(sample_xlsx), sheet="Numbers")
    assert "[Numbers]" in out
    assert "10" in out
    assert "20" in out


def test_read_max_rows_truncates(sample_xlsx):
    out = ExcelTool().run(action="read", file=str(sample_xlsx), max_rows=1)
    assert "more rows" in out


def test_info_lists_sheets(sample_xlsx):
    out = ExcelTool().run(action="info", file=str(sample_xlsx))
    assert "Sheets: 2" in out
    assert "People" in out
    assert "Numbers" in out


def test_sheets_lists_names(sample_xlsx):
    out = ExcelTool().run(action="sheets", file=str(sample_xlsx))
    assert "People" in out
    assert "Numbers" in out


# ─── get_cell / set_cell ─────────────────────────────────────


def test_get_cell_value(sample_xlsx):
    out = ExcelTool().run(action="get_cell", file=str(sample_xlsx), cell="A1")
    assert "Name" in out


def test_get_cell_specific_sheet(sample_xlsx):
    out = ExcelTool().run(action="get_cell", file=str(sample_xlsx), cell="A2", sheet="Numbers")
    assert "10" in out


def test_get_cell_empty(sample_xlsx):
    out = ExcelTool().run(action="get_cell", file=str(sample_xlsx), cell="Z99")
    assert "empty" in out.lower()


def test_get_cell_requires_cell(sample_xlsx):
    out = ExcelTool().run(action="get_cell", file=str(sample_xlsx), cell="")
    assert "required" in out.lower()


def test_set_cell_writes_value(sample_xlsx):
    out = ExcelTool().run(action="set_cell", file=str(sample_xlsx), cell="A2", value="Updated")
    assert "Set A2" in out
    # Verify on disk
    check = ExcelTool().run(action="get_cell", file=str(sample_xlsx), cell="A2")
    assert "Updated" in check


def test_set_cell_coerces_int(sample_xlsx):
    ExcelTool().run(action="set_cell", file=str(sample_xlsx), cell="B2", value="99")
    check = ExcelTool().run(action="get_cell", file=str(sample_xlsx), cell="B2")
    # 99 (int), not "99" (string)
    assert "99" in check
    assert "'99'" not in check  # not quoted as string


def test_set_cell_save_as_keeps_original(sample_xlsx, tmp_path):
    copy_path = tmp_path / "copy.xlsx"
    ExcelTool().run(
        action="set_cell",
        file=str(sample_xlsx),
        cell="A2",
        value="Changed",
        save_as=str(copy_path),
    )
    # Original untouched
    orig = ExcelTool().run(action="get_cell", file=str(sample_xlsx), cell="A2")
    assert "Brijesh" in orig
    # Copy has new value
    new = ExcelTool().run(action="get_cell", file=str(copy_path), cell="A2")
    assert "Changed" in new


# ─── append_row ──────────────────────────────────────────────


def test_append_row(sample_xlsx):
    out = ExcelTool().run(
        action="append_row",
        file=str(sample_xlsx),
        row="Aman, 30, Pune",
    )
    assert "Appended" in out
    # Verify
    check = ExcelTool().run(action="read", file=str(sample_xlsx))
    assert "Aman" in check
    assert "Pune" in check


def test_append_row_requires_row(sample_xlsx):
    out = ExcelTool().run(action="append_row", file=str(sample_xlsx), row="")
    assert "required" in out.lower()


# ─── find_replace ────────────────────────────────────────────


def test_find_replace_makes_edit(sample_xlsx):
    out = ExcelTool().run(
        action="find_replace",
        file=str(sample_xlsx),
        find="Mumbai",
        replace="Bangalore",
    )
    assert "Replaced 1" in out
    check = ExcelTool().run(action="read", file=str(sample_xlsx))
    assert "Bangalore" in check
    assert "Mumbai" not in check


def test_find_replace_no_match(sample_xlsx):
    out = ExcelTool().run(
        action="find_replace",
        file=str(sample_xlsx),
        find="Antarctica",
        replace="Mars",
    )
    assert "not found" in out.lower()


def test_find_replace_specific_sheet_scope(sample_xlsx):
    """Replace only in 'People' should not touch 'Numbers'."""
    # First add a "Mumbai" cell to Numbers sheet to test isolation
    wb = openpyxl.load_workbook(str(sample_xlsx))
    wb["Numbers"]["C1"] = "Mumbai"
    wb.save(str(sample_xlsx))
    wb.close()

    out = ExcelTool().run(
        action="find_replace",
        file=str(sample_xlsx),
        find="Mumbai",
        replace="Delhi",
        sheet="People",
    )
    assert "Replaced" in out
    # Numbers sheet should still have Mumbai
    check = ExcelTool().run(action="read", file=str(sample_xlsx), sheet="Numbers")
    assert "Mumbai" in check


def test_find_replace_requires_find(sample_xlsx):
    out = ExcelTool().run(action="find_replace", file=str(sample_xlsx), find="", replace="x")
    assert "required" in out.lower()
