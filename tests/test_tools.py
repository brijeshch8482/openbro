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


def test_file_open_fuzzy_finds_in_nested_subdir(tmp_path, monkeypatch):
    """Captured failure: user said 'open D:/College Fees Portal 3rd Year' and
    the file was actually at D:/School/College Fees Portal 3rd Year.pdf —
    one folder deep. fuzzy_find now does a bounded recursive walk so this
    works without the user spelling out the full subfolder path."""
    from openbro.tools import file_tool as ft

    sub = tmp_path / "School"
    sub.mkdir()
    target = sub / "College Fees Portal 3rd Year.pdf"
    target.write_bytes(b"pdf")

    called = {}

    def fake_startfile(p):
        called["path"] = p

    monkeypatch.setattr(ft.os, "startfile", fake_startfile, raising=False)
    monkeypatch.setattr(ft.platform, "system", lambda: "Windows")
    out = FileTool().run(action="open", path=str(tmp_path / "College Fees Portal 3rd Year"))
    assert "Opened" in out
    assert called["path"].endswith("College Fees Portal 3rd Year.pdf")


def test_file_search_bounded_does_not_hang_on_deep_tree(tmp_path):
    """Sanity: bounded search returns promptly even when the tree is deeper
    than the depth cap. Catches the rglob freeze on D:\\ from the captured
    session. The cap was bumped from 4 to 8 to handle Android source
    layouts (app/src/main/java/com/example/proj/...). The walker must
    still bail at the new cap rather than chasing 100 levels."""
    import time

    # Build a tree deeper than the (new) depth cap of 8.
    current = tmp_path
    for i in range(15):
        current = current / f"level{i}"
        current.mkdir()
    (current / "deep.pdf").write_bytes(b"x")

    start = time.monotonic()
    out = FileTool().run(action="search", path=str(tmp_path), pattern="deep.pdf")
    elapsed = time.monotonic() - start
    # The file is past the depth cap, so we expect "no files matching"
    # AND the call to be fast (<3s even on slow disks).
    assert elapsed < 3.0, f"bounded search took {elapsed:.1f}s — should be fast"
    assert "No files matching" in out


def test_file_search_skips_noisy_dirs(tmp_path):
    """node_modules / .git / __pycache__ etc. are skipped — they're huge and
    almost never what the user wants. Verify they're excluded."""
    (tmp_path / "user_doc.pdf").write_bytes(b"want this")
    noisy = tmp_path / "node_modules"
    noisy.mkdir()
    (noisy / "user_doc.pdf").write_bytes(b"don't want this")

    out = FileTool().run(action="search", path=str(tmp_path), pattern="user_doc.pdf")
    assert str(tmp_path / "user_doc.pdf") in out
    assert "node_modules" not in out


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


def test_web_search_unwraps_ddg_redirect():
    """DDG wraps result URLs in //duckduckgo.com/l/?uddg=<encoded-url>.
    Real fetch needs the unwrapped target URL."""
    from openbro.tools.web_tool import _unwrap_ddg_redirect

    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fdeveloper.android.com%2Fdocs%2Fmtp&rut=abc123"
    assert _unwrap_ddg_redirect(wrapped) == "https://developer.android.com/docs/mtp"

    # Pass-through for non-redirect URLs
    direct = "https://stackoverflow.com/q/12345"
    assert _unwrap_ddg_redirect(direct) == direct


def test_web_search_parses_ddg_html_results():
    """Smoke test for the SERP parser: extract title + URL + snippet
    from a synthetic DDG HTML response."""
    from openbro.tools.web_tool import _parse_ddg_html

    html = (
        '<div class="result">'
        '<a rel="nofollow" class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdeveloper.android.com%2Fkiosk">'
        "Android Kiosk Mode Guide"
        "</a>"
        '<a class="result__snippet" href="x">'
        "Configure single-app kiosk mode using DevicePolicyManager."
        "</a>"
        "</div>"
    )
    results = _parse_ddg_html(html)
    assert len(results) == 1
    title, url, snippet = results[0]
    assert "Kiosk" in title
    assert url == "https://developer.android.com/kiosk"
    assert "DevicePolicyManager" in snippet


def test_web_search_handles_empty_response():
    """Empty / non-HTML response shouldn't crash the parser."""
    from openbro.tools.web_tool import _parse_ddg_html

    assert _parse_ddg_html("") == []
    assert _parse_ddg_html("random text no markup") == []


def test_web_tool_run_no_max_results_kwarg():
    """Regression guard: a prior caller used to pass max_results=8
    which crashed the tool silently. Tool signature must remain the
    documented set — `engine` was added 2026-05-31 for Bing/Reddit/
    archive support but `max_results` must never come back."""
    import inspect

    sig = inspect.signature(WebTool.run)
    assert set(sig.parameters.keys()) == {"self", "action", "url", "query", "engine"}
    assert "max_results" not in sig.parameters


def test_app_tool_schema():
    tool = AppTool()
    schema = tool.schema()
    assert schema["name"] == "app"
    assert tool.risk == RiskLevel.MODERATE


def test_app_tool_browser_group_resolves_to_known_exes():
    """Captured failure: agent picked chrome.exe for 'close my browser'
    while user was on Brave. 'browser' is now a group alias — tool tries
    every known browser exe and reports which actually closed."""
    from openbro.tools.app_tool import APP_GROUPS_WINDOWS

    assert "chrome.exe" in APP_GROUPS_WINDOWS["browser"]
    assert "brave.exe" in APP_GROUPS_WINDOWS["browser"]
    assert "firefox.exe" in APP_GROUPS_WINDOWS["browser"]
    assert "msedge.exe" in APP_GROUPS_WINDOWS["browser"]
    # Trailing 's' and 'all browsers' should map to the same list.
    assert APP_GROUPS_WINDOWS["browsers"] == APP_GROUPS_WINDOWS["browser"]
    assert APP_GROUPS_WINDOWS["all browsers"] == APP_GROUPS_WINDOWS["browser"]


def test_app_tool_close_group_summarizes_result(monkeypatch):
    """_close_app_group should report which exes closed, which were not
    running, and which errored — one tool call instead of forcing the
    agent to make 7 calls (saves tokens, avoids rate limit)."""
    from openbro.tools import app_tool as at

    class FakeResult:
        def __init__(self, rc, stderr=""):
            self.returncode = rc
            self.stderr = stderr
            self.stdout = ""

    call_log = []

    def fake_run(cmd, **kwargs):
        # taskkill /F /IM <exe>
        exe = cmd[-1]
        call_log.append(exe)
        if exe == "chrome.exe":
            return FakeResult(0)
        if exe == "firefox.exe":
            return FakeResult(0)
        return FakeResult(128, stderr=f'ERROR: The process "{exe}" not found.')

    monkeypatch.setattr(at.platform, "system", lambda: "Windows")
    monkeypatch.setattr(at.subprocess, "run", fake_run)

    tool = at.AppTool()
    out = tool.run(action="close", app_name="browser")

    # Tool tried each browser exe
    assert "chrome.exe" in call_log
    assert "brave.exe" in call_log
    # Closed ones are reported
    assert "Closed:" in out
    assert "chrome.exe" in out
    assert "firefox.exe" in out
    # Not-running ones reported separately, not as errors
    assert "Not running:" in out
    assert "brave.exe" in out


def test_app_tool_close_specific_app_skips_group_logic(monkeypatch):
    """Belt + suspenders: 'close chrome' (specific) still goes through
    the single-app path. Group handling only kicks in for category words."""
    from openbro.tools import app_tool as at

    class FakeResult:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(at.platform, "system", lambda: "Windows")
    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: FakeResult())

    out = at.AppTool().run(action="close", app_name="chrome")
    assert "Closed:" in out
    assert "chrome.exe" in out


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


def test_process_find_requires_query():
    out = ProcessTool().run(action="find", query="")
    assert "Query required" in out


def test_process_find_windows_matches_command_line(monkeypatch):
    """Captured failure: 'find query=claude' returned 'no processes' even
    though Claude Code was running as node.exe with 'claude' in the args.
    The new Windows impl uses Get-CimInstance + JSON so command-line
    substrings match. Mock the PowerShell call and verify formatting."""
    from openbro.tools import process_tool as pt

    fake_json = (
        '[{"ProcessId":1234,"Name":"node.exe",'
        '"CommandLine":"node /usr/bin/claude --resume abc"},'
        '{"ProcessId":5678,"Name":"sh.exe",'
        '"CommandLine":"sh -c claude"}]'
    )

    class FakeResult:
        stdout = fake_json
        stderr = ""
        returncode = 0

    monkeypatch.setattr(pt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pt.subprocess, "run", lambda *a, **kw: FakeResult())

    out = ProcessTool().run(action="find", query="claude")
    assert "Found 2" in out
    assert "PID 1234" in out
    assert "node.exe" in out
    assert "PID 5678" in out
    assert "sh.exe" in out


def test_process_find_windows_no_matches_returns_actionable_hint(monkeypatch):
    """Empty result should suggest looking at adjacent process names rather
    than silently say 'not found' — the agent has bailed out on this exact
    case before."""
    from openbro.tools import process_tool as pt

    class FakeResult:
        stdout = ""
        stderr = ""
        returncode = 0

    monkeypatch.setattr(pt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pt.subprocess, "run", lambda *a, **kw: FakeResult())

    out = ProcessTool().run(action="find", query="claude")
    assert "claude" in out
    assert "synonym" in out or "node" in out


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
    # 22 tools: + elevate (UAC-prompt admin shell — captured 2026-06-20
    # when the user noticed OpenBro silently swallowed "Access denied"
    # on C:/Windows/Temp instead of asking for elevation). document
    # was the previous addition for the universal file reader. sticky_notes
    # is still the carve-out for Windows Sticky Notes app integration.
    assert len(BUILTIN_TOOLS) == 22


def test_tool_registry_schema():
    registry = ToolRegistry()
    schemas = registry.get_tools_schema()
    assert len(schemas) == 22
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
