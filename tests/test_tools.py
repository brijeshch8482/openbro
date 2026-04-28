"""Tests for built-in tools."""

from openbro.tools.file_tool import FileTool
from openbro.tools.shell_tool import ShellTool
from openbro.tools.system_tool import SystemTool
from openbro.tools.web_tool import WebTool
from openbro.tools.registry import ToolRegistry


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


def test_tool_registry():
    registry = ToolRegistry()
    tools = registry.list_tools()
    assert "file_ops" in tools
    assert "shell" in tools
    assert "system_info" in tools
    assert "web" in tools


def test_tool_registry_schema():
    registry = ToolRegistry()
    schemas = registry.get_tools_schema()
    assert len(schemas) == 4
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
