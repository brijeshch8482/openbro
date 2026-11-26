"""Tool registry - manages all available tools with risk-based execution."""

from openbro.tools.app_tool import AppTool
from openbro.tools.base import BaseTool, RiskLevel
from openbro.tools.browser_tool import BrowserTool
from openbro.tools.cli_agent_tool import CliAgentTool
from openbro.tools.clipboard_tool import ClipboardTool
from openbro.tools.datetime_tool import DateTimeTool
from openbro.tools.docs_tool import DocsTool
from openbro.tools.document_tool import DocumentTool
from openbro.tools.download_tool import DownloadTool
from openbro.tools.elevate_tool import ElevateTool
from openbro.tools.excel_tool import ExcelTool
from openbro.tools.file_tool import FileTool
from openbro.tools.memory_tool import MemoryTool
from openbro.tools.network_tool import NetworkTool
from openbro.tools.notification_tool import NotificationTool
from openbro.tools.process_tool import ProcessTool
from openbro.tools.python_tool import PythonTool
from openbro.tools.screenshot_tool import ScreenshotTool
from openbro.tools.search_tools import EditTool, GlobTool, GrepTool, ReadTool
from openbro.tools.shell_tool import ShellTool
from openbro.tools.sticky_notes_tool import StickyNotesTool
from openbro.tools.system_control_tool import SystemControlTool
from openbro.tools.system_tool import SystemTool
from openbro.tools.task_tool import TaskTool
from openbro.tools.web_tool import WebTool
from openbro.tools.word_tool import WordTool
from openbro.utils.audit import log_tool_execution

BUILTIN_TOOLS = [
    AppTool,
    BrowserTool,
    CliAgentTool,
    ClipboardTool,
    DateTimeTool,
    DocsTool,
    DocumentTool,
    DownloadTool,
    EditTool,
    ElevateTool,
    ExcelTool,
    FileTool,
    GlobTool,
    GrepTool,
    MemoryTool,
    NetworkTool,
    NotificationTool,
    ProcessTool,
    PythonTool,
    ReadTool,
    ScreenshotTool,
    ShellTool,
    StickyNotesTool,
    SystemControlTool,
    SystemTool,
    TaskTool,
    WebTool,
    WordTool,
]


# Deferred by default in `auto` mode: big schemas, rarely needed.
DEFAULT_DEFERRED = ("cli_agent", "sticky_notes", "notification")


class ToolRegistry:
    def __init__(self, config: dict | None = None):
        self._tools: dict[str, BaseTool] = {}
        self._skill_registry = None
        # Progressive disclosure (harness piece 6): deferred tools are
        # listed by name in the prompt; their schemas are sent only after
        # `tool_search` loads them.
        self._deferred: set[str] = set()
        self._loaded: set[str] = set()
        self._register_builtins()
        self._builtin_names = set(self._tools)
        if config is not None:
            self._register_skills(config)
            self.configure_loading(config)

    def _register_builtins(self):
        for tool_cls in BUILTIN_TOOLS:
            tool = tool_cls()
            self._tools[tool.name] = tool

    # ─── progressive disclosure ─────────────────────────────────────

    def configure_loading(self, config: dict) -> None:
        """`agent.tool_loading`: auto (default) defers extension tools
        (skills, MCP) plus `agent.deferred_tools`; all sends every schema."""
        agent = (config or {}).get("agent", {}) or {}
        mode = str(agent.get("tool_loading", "auto")).lower()
        self._loaded.clear()
        if mode == "all":
            self._deferred = set()
        else:
            extra = agent.get("deferred_tools")
            names = set(DEFAULT_DEFERRED if extra is None else extra)
            extensions = set(self._tools) - self._builtin_names - {"tool_search"}
            self._deferred = (names | extensions) & set(self._tools)
        if self._deferred:
            from openbro.tools.tool_search import ToolSearchTool

            self._tools["tool_search"] = ToolSearchTool(self)
        else:
            self._tools.pop("tool_search", None)

    def view(self, allowed: set[str]) -> "ToolRegistry":
        """A registry holding only `allowed` tools (shared instances) —
        what a sub-agent gets. Deferred/loaded state is copied."""
        clone = ToolRegistry.__new__(ToolRegistry)
        clone._tools = {n: t for n, t in self._tools.items() if n in allowed and n != "tool_search"}
        clone._skill_registry = None
        clone._builtin_names = self._builtin_names & set(clone._tools)
        clone._deferred = self._deferred & set(clone._tools)
        clone._loaded = self._loaded & clone._deferred
        if clone._deferred - clone._loaded:
            from openbro.tools.tool_search import ToolSearchTool

            clone._tools["tool_search"] = ToolSearchTool(clone)
        return clone

    def deferred_names(self) -> set[str]:
        """Deferred tools not loaded yet."""
        return self._deferred - self._loaded

    def loaded_names(self) -> set[str]:
        return set(self._loaded)

    def load(self, names) -> None:
        self._loaded |= set(names) & self._deferred

    def is_active(self, name: str) -> bool:
        return name in self._tools and (name not in self._deferred or name in self._loaded)

    def active_tools(self) -> list[str]:
        return [n for n in self._tools if self.is_active(n)]

    def deferred_index(self) -> list[str]:
        """One line per deferred tool for the system prompt."""
        lines = []
        for name in sorted(self._deferred):
            desc = " ".join((self._tools[name].description or "").split())
            first = desc.split(". ")[0][:140]
            lines.append(f"- {name} — {first}")
        return lines

    def _register_skills(self, config: dict):
        from openbro.skills.registry import SkillRegistry

        self._skill_registry = SkillRegistry(config=config)
        for tool in self._skill_registry.all_tools(only_configured=True):
            self._tools[tool.name] = tool

        # MCP servers — connect and inject their tools too
        try:
            self._register_mcp(config)
        except Exception:
            pass

    def _register_mcp(self, config: dict):
        servers_cfg = (config.get("mcp", {}) or {}).get("servers", []) or []
        if not servers_cfg:
            return
        from openbro.mcp.client import MCPServerConfig, register_mcp_tools

        configs = []
        for raw in servers_cfg:
            if not isinstance(raw, dict):
                continue
            cmd = raw.get("command")
            if not cmd:
                continue
            configs.append(
                MCPServerConfig(
                    name=raw.get("name", "mcp"),
                    command=list(cmd),
                    cwd=raw.get("cwd"),
                    env=raw.get("env", {}) or {},
                    enabled=raw.get("enabled", True),
                )
            )
        if configs:
            register_mcp_tools(configs, self)

    def skills_info(self) -> list[dict]:
        if not self._skill_registry:
            return []
        return self._skill_registry.info()

    def get_tools_schema(self) -> list[dict]:
        """Schemas of the active tools (deferred ones only once loaded)."""
        return [tool.schema() for name, tool in self._tools.items() if self.is_active(name)]

    def get_all_tools_schema(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_risk(self, name: str, args: dict | None = None) -> str:
        """Return the risk tier for `name`, optionally adjusted by the
        actual args. `BaseTool.compute_risk(args)` is what catches a
        destructive `shell` invocation that the class-level tier would
        otherwise label as merely MODERATE."""
        tool = self._tools.get(name)
        if not tool:
            return RiskLevel.SAFE.value
        if args is not None and hasattr(tool, "compute_risk"):
            try:
                risk = tool.compute_risk(args)
            except Exception:
                risk = tool.risk
        else:
            risk = tool.risk
        return risk.value if isinstance(risk, RiskLevel) else str(risk)

    def execute(self, name: str, args: dict, confirmed: bool = False) -> str:
        tool = self._tools.get(name)
        if not tool:
            log_tool_execution(name, args, "Unknown tool", risk="unknown")
            return f"Unknown tool: {name}"
        if name in self._deferred:
            self._loaded.add(name)  # called directly (text mode) — treat as loaded
        try:
            result = tool.run(**args)
        except Exception as e:
            result = f"Tool error ({name}): {e}"

        log_tool_execution(
            name,
            args,
            result,
            risk=self.get_risk(name, args=args),
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
