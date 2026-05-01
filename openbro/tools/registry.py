"""Tool registry - manages all available tools with risk-based execution."""

from openbro.tools.app_tool import AppTool
from openbro.tools.base import BaseTool, RiskLevel
from openbro.tools.browser_tool import BrowserTool
from openbro.tools.clipboard_tool import ClipboardTool
from openbro.tools.datetime_tool import DateTimeTool
from openbro.tools.download_tool import DownloadTool
from openbro.tools.file_tool import FileTool
from openbro.tools.memory_tool import MemoryTool
from openbro.tools.network_tool import NetworkTool
from openbro.tools.notification_tool import NotificationTool
from openbro.tools.process_tool import ProcessTool
from openbro.tools.screenshot_tool import ScreenshotTool
from openbro.tools.shell_tool import ShellTool
from openbro.tools.system_control_tool import SystemControlTool
from openbro.tools.system_tool import SystemTool
from openbro.tools.web_tool import WebTool
from openbro.utils.audit import log_tool_execution

BUILTIN_TOOLS = [
    AppTool,
    BrowserTool,
    ClipboardTool,
    DateTimeTool,
    DownloadTool,
    FileTool,
    MemoryTool,
    NetworkTool,
    NotificationTool,
    ProcessTool,
    ScreenshotTool,
    ShellTool,
    SystemControlTool,
    SystemTool,
    WebTool,
]


class ToolRegistry:
    def __init__(self, config: dict | None = None):
        self._tools: dict[str, BaseTool] = {}
        self._skill_registry = None
        self._register_builtins()
        if config is not None:
            self._register_skills(config)

    def _register_builtins(self):
        for tool_cls in BUILTIN_TOOLS:
            tool = tool_cls()
            self._tools[tool.name] = tool

    def _register_skills(self, config: dict):
        from openbro.skills.registry import SkillRegistry

        self._skill_registry = SkillRegistry(config=config)
        for tool in self._skill_registry.all_tools(only_configured=True):
            self._tools[tool.name] = tool

    def skills_info(self) -> list[dict]:
        if not self._skill_registry:
            return []
        return self._skill_registry.info()

    def get_tools_schema(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_risk(self, name: str) -> str:
        tool = self._tools.get(name)
        if not tool:
            return RiskLevel.SAFE.value
        return tool.risk.value if isinstance(tool.risk, RiskLevel) else str(tool.risk)

    def execute(self, name: str, args: dict, confirmed: bool = False) -> str:
        tool = self._tools.get(name)
        if not tool:
            log_tool_execution(name, args, "Unknown tool", risk="unknown")
            return f"Unknown tool: {name}"
        try:
            result = tool.run(**args)
        except Exception as e:
            result = f"Tool error ({name}): {e}"

        log_tool_execution(
            name,
            args,
            result,
            risk=self.get_risk(name),
            confirmed=confirmed,
        )
        return result

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def list_tools_by_risk(self) -> dict[str, list[str]]:
        result = {"safe": [], "moderate": [], "dangerous": []}
        for name, tool in self._tools.items():
            risk = tool.risk.value if isinstance(tool.risk, RiskLevel) else str(tool.risk)
            if risk in result:
                result[risk].append(name)
        return result
