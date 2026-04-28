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
    assert len(BUILTIN_TOOLS) == 14


def test_tool_registry_schema():
    registry = ToolRegistry()
    schemas = registry.get_tools_schema()
    assert len(schemas) == 14
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
    assert registry.get_risk("shell") == "dangerous"
    assert registry.get_risk("system_control") == "dangerous"


def test_tool_registry_list_by_risk():
    registry = ToolRegistry()
    by_risk = registry.list_tools_by_risk()
    assert "shell" in by_risk["dangerous"]
    assert "system_control" in by_risk["dangerous"]
    assert "file_ops" in by_risk["moderate"]
    assert "system_info" in by_risk["safe"]
