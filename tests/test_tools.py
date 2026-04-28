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


def test_shell_tool_blocks_dangerous():
    tool = ShellTool()
    result = tool.run(command="rm -rf /")
    assert "BLOCKED" in result


def test_shell_tool_runs_safe_command():
    tool = ShellTool()
    result = tool.run(command="echo hello")
    assert "hello" in result


def test_system_tool_os():
    tool = SystemTool()
    result = tool.run(info_type="os")
    assert "OS:" in result


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
