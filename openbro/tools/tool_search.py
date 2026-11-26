"""`tool_search` — load deferred tools on demand (harness piece 6).

Deferred tools appear in the system prompt as one line each (name + what
it does). Their full schemas are only sent once the model loads them with
this tool, which keeps every call smaller.
"""

from __future__ import annotations

from openbro.core.tool_protocol import render_tool_instructions
from openbro.memory.ranking import bm25_scores
from openbro.tools.base import BaseTool, RiskLevel


class ToolSearchTool(BaseTool):
    name = "tool_search"
    description = (
        "Load tools listed under MORE TOOLS in the system prompt before using "
        "them. Pass select='name1,name2' to load by name, or query='what you "
        "need' to find matching tools. Returns their full parameters; after "
        "that, call them like any other tool."
    )
    risk = RiskLevel.SAFE

    def __init__(self, registry=None):
        self._registry = registry

    def run(self, select: str = "", query: str = "", limit: int = 5) -> str:
        reg = self._registry
        if reg is None:
            return "tool_search is not connected to a tool registry."
        pending = reg.deferred_names()
        if select:
            wanted = [n.strip() for n in select.split(",") if n.strip()]
            unknown = [n for n in wanted if n not in reg.list_tools()]
            names = [n for n in wanted if n in reg.list_tools()]
        elif query:
            candidates = sorted(pending | reg.loaded_names())
            docs = [f"{n} {reg.get_tool(n).description}" for n in candidates]
            scored = sorted(zip(candidates, bm25_scores(query, docs)), key=lambda p: -p[1])
            names = [n for n, s in scored if s > 0][: max(1, min(int(limit or 5), 10))]
            unknown = []
        else:
            return "Pass select='tool_name' or query='what you need'."
        if not names:
            return "No matching tools." + (f" Unknown: {', '.join(unknown)}" if unknown else "")
        reg.load(names)
        schemas = [reg.get_tool(n).schema() for n in names]
        text = render_tool_instructions(schemas)
        # Keep only the per-tool part; the calling format is already known.
        text = text[text.index("Available tools:") + len("Available tools:") :].strip()
        out = f"Loaded {', '.join(names)}. You can call them now.\n\n{text}"
        if unknown:
            out += f"\n\nUnknown: {', '.join(unknown)}"
        return out

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "select": {"type": "string", "description": "Tool names, comma-separated"},
                    "query": {"type": "string", "description": "What the tool should do"},
                    "limit": {"type": "integer", "description": "Max tools for a query (5)"},
                },
            },
        }
