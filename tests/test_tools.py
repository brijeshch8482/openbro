"""Tests for built-in tools."""

from openbro.tools.app_tool import AppTool
from openbro.tools.base import RiskLevel
from openbro.tools.browser_tool import BrowserTool
from openbro.tools.clipboard_tool import ClipboardTool
from openbro.tools.datetime_tool import DateTimeTool
from openbro.tools.download_tool import DownloadTool
from openbro.tools.file_tool import FileTool
from openbro.tools.network_tool import NetworkTool
from openbro.tools.notification_tool import NotificationTool
from openbro.tools.process_tool import ProcessTool
from openbro.tools.registry import BUILTIN_TOOLS, ToolRegistry
from openbro.tools.screenshot_tool import ScreenshotTool
from openbro.tools.shell_tool import ShellTool
from openbro.tools.system_control_tool import SystemControlTool
from openbro.tools.system_tool import SystemTool
from openbro.tools.web_tool import WebTool


def test_file_tool_list():
    tool = FileTool()
    result = tool.run(action="list", path=".")
    assert "Contents of" in result or "Empty directory" in result


def test_file_tool_read_nonexistent():
    tool = FileTool()
    result = tool.run(action="read", path="/tmp/nonexistent_openbro_test_file.txt")
    assert "not found" in result.lower()


def test_file_tool_schema():
    tool = FileTool()
    schema = tool.schema()
    assert schema["name"] == "file_ops"
    assert "parameters" in schema


def test_file_open_fuzzy_matches_unique_basename(tmp_path, monkeypatch):
    """open 'T&P fees' should find 'T&P fees.pdf' without the user spelling
    out the extension. Real captured failure: agent kept asking
    'kya extension hai?' instead of trying the obvious match."""
    from openbro.tools import file_tool as ft

    target = tmp_path / "T&P fees.pdf"
    target.write_bytes(b"%PDF dummy")

    called = {}

    def fake_startfile(p):
        called["path"] = p

    monkeypatch.setattr(ft.os, "startfile", fake_startfile, raising=False)
    monkeypatch.setattr(ft.platform, "system", lambda: "Windows")

    out = FileTool().run(action="open", path=str(tmp_path / "T&P fees"))
    assert "Opened" in out
    assert called.get("path", "").endswith("T&P fees.pdf")


def test_file_open_ambiguous_basename_lists_matches(tmp_path, monkeypatch):
    """Two files share the basename — tool must list both and ask, not silently
    pick one (that's how you open the wrong document)."""
    from openbro.tools import file_tool as ft

    (tmp_path / "report.pdf").write_bytes(b"a")
    (tmp_path / "report.docx").write_bytes(b"b")

    monkeypatch.setattr(ft.platform, "system", lambda: "Windows")

    out = FileTool().run(action="open", path=str(tmp_path / "report"))
    assert "Multiple files match" in out
    assert "report.pdf" in out
    assert "report.docx" in out


def test_file_open_no_matches_anywhere(tmp_path, monkeypatch):
    """No match in parent OR common roots → return actionable error,
    don't pretend success."""
    from openbro.tools import file_tool as ft

    monkeypatch.setattr(ft, "_COMMON_SEARCH_ROOTS", [tmp_path])
    monkeypatch.setattr(ft.platform, "system", lambda: "Windows")
    out = FileTool().run(action="open", path=str(tmp_path / "definitely_not_here"))
    assert "not found" in out.lower() or "no fuzzy matches" in out.lower()


def test_file_open_exact_path_skips_fuzzy(tmp_path, monkeypatch):
    """If the literal path exists, don't go searching — open it directly.
    Belt + suspenders against the fuzzy logic stealing valid open calls."""
    from openbro.tools import file_tool as ft

    target = tmp_path / "exact.txt"
    target.write_text("hi")

    called = {}

    def fake_startfile(p):
        called["path"] = p

    monkeypatch.setattr(ft.os, "startfile", fake_startfile, raising=False)
    monkeypatch.setattr(ft.platform, "system", lambda: "Windows")
    out = FileTool().run(action="open", path=str(target))
    assert "Opened" in out
    assert called["path"].endswith("exact.txt")


def test_shell_tool_blocks_dangerous():
    tool = ShellTool()
    result = tool.run(command="rm -rf /")
    assert "BLOCKED" in result


def test_shell_tool_runs_safe_command():
    tool = ShellTool()
    result = tool.run(command="echo hello")
    assert "hello" in result


def test_shell_tool_schema():
    tool = ShellTool()
    schema = tool.schema()
    assert schema["name"] == "shell"


def test_system_tool_os():
    tool = SystemTool()
    result = tool.run(info_type="os")
    assert "OS:" in result


def test_system_tool_disk():
    tool = SystemTool()
    result = tool.run(info_type="disk")
    assert "Disk" in result or "unavailable" in result


def test_system_tool_all():
    tool = SystemTool()
    result = tool.run(info_type="all")
    assert "OS:" in result


def test_web_tool_schema():
    tool = WebTool()
    schema = tool.schema()
    assert schema["name"] == "web"
    assert "fetch" in str(schema)


def test_app_tool_schema():
    tool = AppTool()
    schema = tool.schema()
    assert schema["name"] == "app"
    assert tool.risk == RiskLevel.MODERATE


def test_app_tool_unknown_action():
    tool = AppTool()
    result = tool.run(action="invalid")
    assert "Unknown action" in result


def test_browser_tool_schema():
    tool = BrowserTool()
    schema = tool.schema()
    assert schema["name"] == "browser"
    assert tool.risk == RiskLevel.MODERATE


def test_browser_tool_search_unknown_engine():
    tool = BrowserTool()
    result = tool.run(action="search", query="test", engine="unknown_engine")
    assert "Unknown engine" in result


def test_download_tool_schema():
    tool = DownloadTool()
    schema = tool.schema()
    assert schema["name"] == "download"
    assert tool.risk == RiskLevel.MODERATE


def test_download_tool_invalid_url():
    tool = DownloadTool()
    result = tool.run(url="not-a-url")
    assert "Invalid URL" in result


def test_clipboard_tool_schema():
    tool = ClipboardTool()
    schema = tool.schema()
    assert schema["name"] == "clipboard"
    assert tool.risk == RiskLevel.SAFE


def test_clipboard_tool_unknown_action():
    tool = ClipboardTool()
    result = tool.run(action="invalid")
    assert "Unknown action" in result


def test_screenshot_tool_schema():
    tool = ScreenshotTool()
    schema = tool.schema()
    assert schema["name"] == "screenshot"
    assert tool.risk == RiskLevel.SAFE


def test_notification_tool_schema():
    tool = NotificationTool()
    schema = tool.schema()
    assert schema["name"] == "notification"


def test_notification_tool_no_title():
    tool = NotificationTool()
    result = tool.run(title="")
    assert "Title required" in result


def test_process_tool_schema():
    tool = ProcessTool()
    schema = tool.schema()
    assert schema["name"] == "process"
    assert tool.risk == RiskLevel.MODERATE


def test_process_tool_unknown_action():
    tool = ProcessTool()
    result = tool.run(action="invalid")
    assert "Unknown action" in result


def test_system_control_tool_schema():
    tool = SystemControlTool()
    schema = tool.schema()
    assert schema["name"] == "system_control"
    assert tool.risk == RiskLevel.DANGEROUS


def test_system_control_unknown_action():
    tool = SystemControlTool()
    result = tool.run(action="invalid")
    assert "Unknown action" in result


def test_system_control_volume_invalid():
    tool = SystemControlTool()
    result = tool.run(action="volume", value=200)
    assert "between 0 and 100" in result


def test_network_tool_schema():
    tool = NetworkTool()
    schema = tool.schema()
    assert schema["name"] == "network"
    assert tool.risk == RiskLevel.SAFE


def test_network_tool_unknown_action():
    tool = NetworkTool()
    result = tool.run(action="invalid")
    assert "Unknown action" in result


def test_datetime_tool_schema():
    tool = DateTimeTool()
    schema = tool.schema()
    assert schema["name"] == "datetime"
    assert tool.risk == RiskLevel.SAFE


def test_datetime_tool_now():
    tool = DateTimeTool()
    result = tool.run(action="now")
    assert "Date:" in result and "Time:" in result


def test_datetime_tool_weekday():
    tool = DateTimeTool()
    result = tool.run(action="weekday")
    assert "Today is" in result


def test_tool_registry():
    registry = ToolRegistry()
    tools = registry.list_tools()
    # All v0.1 tools
    assert "file_ops" in tools
    assert "shell" in tools
    assert "system_info" in tools
    assert "web" in tools
    # New v0.2 tools
    assert "app" in tools
    assert "browser" in tools
    assert "download" in tools
    assert "clipboard" in tools
    assert "screenshot" in tools
    assert "notification" in tools
    assert "process" in tools
    assert "system_control" in tools
    assert "network" in tools
    assert "datetime" in tools


def test_tool_registry_count():
    # 21 tools: + document for the universal file reader (PDF/image OCR/
    # audio transcript/HTML/CSV/zip — replaces the old "main sirf .docx
    # padh sakta" failure mode). sticky_notes is still the carve-out for
    # Windows Sticky Notes app integration.
    assert len(BUILTIN_TOOLS) == 21


def test_tool_registry_schema():
    registry = ToolRegistry()
    schemas = registry.get_tools_schema()
    assert len(schemas) == 21
    for s in schemas:
        assert "name" in s
        assert "parameters" in s


def test_tool_registry_execute():
    registry = ToolRegistry()
    result = registry.execute("system_info", {"info_type": "os"})
    assert "OS:" in result


def test_tool_registry_unknown_tool():
    registry = ToolRegistry()
    result = registry.execute("nonexistent", {})
    assert "Unknown tool" in result


def test_tool_registry_get_risk():
    registry = ToolRegistry()
    assert registry.get_risk("system_info") == "safe"
    assert registry.get_risk("file_ops") == "moderate"
    # shell + python are MODERATE so the LLM uses them freely for ad-hoc
    # queries (BLOCKED_PATTERNS still guards rm -rf / format / etc.).
    assert registry.get_risk("shell") == "moderate"
    assert registry.get_risk("python") == "moderate"
    assert registry.get_risk("system_control") == "dangerous"


def test_tool_registry_list_by_risk():
    registry = ToolRegistry()
    by_risk = registry.list_tools_by_risk()
    assert "system_control" in by_risk["dangerous"]
    assert "shell" in by_risk["moderate"]
    assert "python" in by_risk["moderate"]
    assert "file_ops" in by_risk["moderate"]
    assert "system_info" in by_risk["safe"]


def test_resolve_user_path_prefers_onedrive_desktop(monkeypatch, tmp_path):
    """When a path under home/Desktop is asked and OneDrive Desktop exists, redirect."""
    import platform

    from openbro.utils import paths as paths_mod
    from openbro.utils.paths import resolve_user_path

    if platform.system() != "Windows":
        import pytest

        pytest.skip("OneDrive redirect only applies on Windows")

    # Build a fake home + OneDrive layout
    fake_home = tmp_path / "Users" / "test"
    fake_home.mkdir(parents=True)
    (fake_home / "Desktop").mkdir()  # system Desktop (legacy, empty)
    fake_onedrive = fake_home / "OneDrive"
    (fake_onedrive / "Desktop").mkdir(parents=True)  # real Desktop

    # Patch BOTH the home() reference inside paths.py and the onedrive
    # detector. We don't rely on expanduser() because it reads USERPROFILE
    # from the real env at C-level.
    monkeypatch.setattr(paths_mod.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(paths_mod, "_onedrive_roots", lambda: [fake_onedrive])

    resolved = resolve_user_path(str(fake_home / "Desktop" / "new.docx"))
    assert "OneDrive" in str(resolved), f"expected OneDrive Desktop, got {resolved}"


def test_resolve_user_path_absolute_unchanged(tmp_path):
    from openbro.utils.paths import resolve_user_path

    abs_path = tmp_path / "some.docx"
    resolved = resolve_user_path(str(abs_path))
    assert resolved == abs_path.resolve()


def test_word_tool_create(tmp_path):
    import pytest

    pytest.importorskip("docx", reason="python-docx not installed (openbro[office])")
    from openbro.tools.word_tool import WordTool

    target = tmp_path / "subdir" / "note.docx"
    tool = WordTool()
    result = tool.run(action="create", file=str(target), text="kal mai office nahi jaunga")
    assert "Created" in result
    assert target.exists()
    # Verify text round-trips
    read_back = tool.run(action="read", file=str(target))
    assert "kal mai office" in read_back


def test_word_tool_create_existing_blocked(tmp_path):
    import pytest

    pytest.importorskip("docx", reason="python-docx not installed (openbro[office])")
    from openbro.tools.word_tool import WordTool

    target = tmp_path / "exists.docx"
    target.write_bytes(b"")
    tool = WordTool()
    result = tool.run(action="create", file=str(target))
    assert "already exists" in result


def test_excel_tool_create(tmp_path):
    import pytest

    pytest.importorskip("openpyxl", reason="openpyxl not installed (openbro[office])")
    from openbro.tools.excel_tool import ExcelTool

    target = tmp_path / "subdir" / "data.xlsx"
    tool = ExcelTool()
    result = tool.run(action="create", file=str(target), row="Name,Age,City")
    assert "Created" in result
    assert target.exists()


def test_python_tool_runs_simple_script():
    registry = ToolRegistry()
    result = registry.execute("python", {"code": "print(2 + 2)"}, confirmed=True)
    assert "4" in result


def test_python_tool_blocks_destructive_patterns():
    registry = ToolRegistry()
    result = registry.execute(
        "python", {"code": "import os; os.system('rm -rf /')"}, confirmed=True
    )
    assert "BLOCKED" in result
