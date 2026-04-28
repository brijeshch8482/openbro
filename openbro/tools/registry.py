"""Tool registry - manages all available tools."""

from openbro.tools.file_tool import FileTool
from openbro.tools.shell_tool import ShellTool
from openbro.tools.system_tool import SystemTool
from openbro.tools.web_tool import WebTool


class ToolRegistry:
    def __init__(self):
        self._tools: dict = {}
        self._register_builtins()

    def _register_builtins(self):
        for tool_cls in [FileTool, ShellTool, SystemTool, WebTool]:
            tool = tool_cls()
            self._tools[tool.name] = tool

    def get_tools_schema(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, args: dict) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Unknown tool: {name}"
        try:
            return tool.run(**args)
        except Exception as e:
            return f"Tool error ({name}): {e}"

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())
