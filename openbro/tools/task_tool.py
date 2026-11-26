"""`task` tool — hand a self-contained job to a sub-agent (harness piece 7).

The sub-agent starts with a fresh context, works through the job with
tools, and returns only its final report. The parent's context gets the
answer without every file listing and tool output the sub-agent read on
the way — that is the point: big searches stay out of the main history.

Types:
- `explore`: read-only tools (safe risk only) — searching, reading,
  looking things up.
- `general`: every tool except `task` itself. Risky actions still go
  through the same permission gate, so the user is asked as usual.
"""

from __future__ import annotations

from openbro.tools.base import BaseTool, RiskLevel

SUBAGENT_PROMPT = (
    "## YOU ARE A SUB-AGENT\n"
    "The main OpenBro agent gave you one task. Work through it with your "
    "tools until it is done. The user does not see your messages — your "
    "final reply goes back to the main agent, so make it a compact report: "
    "what you found or did, with exact file paths, numbers and names. No "
    "greetings, no questions back; if something blocks you, say what and "
    "why in the report."
)
EXPLORE_NOTE = (
    "You have read-only tools. Search broadly (grep, glob, read, docs, web), "
    "then report. Do not try to change anything."
)
MAX_REPORT_CHARS = 8000


class TaskTool(BaseTool):
    name = "task"
    description = (
        "Give a self-contained job to a sub-agent that works with its own "
        "fresh context and returns only a final report. Use it for broad "
        "searches or multi-step research whose intermediate output you don't "
        "need to keep (e.g. 'find every file that mentions the fee report and "
        "summarise what each says'). agent_type 'explore' = read-only tools "
        "(default); 'general' = all tools, risky actions still need the "
        "user's permission. Write the prompt as a complete brief: the goal, "
        "where to look, and what the report must contain."
    )
    risk = RiskLevel.SAFE

    def __init__(self) -> None:
        self._parent = None  # set by the owning Agent

    def run(self, prompt: str, description: str = "", agent_type: str = "explore") -> str:
        parent = self._parent
        if parent is None:
            return "task tool is not connected to an agent."
        if not (prompt or "").strip():
            return "task needs a 'prompt' describing the job."
        kind = agent_type if agent_type in ("explore", "general") else "explore"

        from openbro.core.agent import Agent
        from openbro.memory import MemoryManager
        from openbro.memory.transcript import InMemoryTranscriptStore
        from openbro.utils.config import load_config

        registry = parent.tool_registry
        if kind == "explore":
            allowed = {
                n
                for n in registry.list_tools()
                if registry.get_risk(n) == RiskLevel.SAFE.value and n not in ("task", "memory")
            }
        else:
            allowed = set(registry.list_tools()) - {"task"}
        view = registry.view(allowed)

        steps = int((load_config().get("agent", {}) or {}).get("subagent_max_steps", 15))
        role = SUBAGENT_PROMPT + ("\n" + EXPLORE_NOTE if kind == "explore" else "")
        label = description or prompt.strip().splitlines()[0][:60]
        parent.bus.emit(
            "subagent", f"sub-agent ({kind}) started: {label}", agent_type=kind, state="start"
        )
        child = Agent(
            memory=MemoryManager(
                user_id=parent.memory.user_id,
                channel="subagent",
                transcripts=InMemoryTranscriptStore(),
            ),
            interactive=parent.interactive,
            permission_gate=parent.permissions,
            provider=parent.provider,
            tool_registry=view,
            role_prompt=role,
            max_steps=steps,
        )
        child.playbooks_enabled = False
        try:
            report = child.chat(prompt)
        finally:
            parent.session_tokens_in += child.session_tokens_in
            parent.session_tokens_out += child.session_tokens_out
        tools_used = sum(1 for m in child.history if m.role == "tool")
        parent.bus.emit(
            "subagent",
            f"sub-agent ({kind}) done: {tools_used} tool calls",
            agent_type=kind,
            state="end",
        )
        if len(report) > MAX_REPORT_CHARS:
            report = report[:MAX_REPORT_CHARS] + "\n…[report truncated]"
        return f"Sub-agent ({kind}) report — {tools_used} tool calls:\n\n{report}"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Complete brief: goal, where to look, what to report",
                    },
                    "description": {
                        "type": "string",
                        "description": "3-6 word label shown to the user",
                    },
                    "agent_type": {
                        "type": "string",
                        "enum": ["explore", "general"],
                        "description": "explore = read-only (default); general = all tools",
                    },
                },
                "required": ["prompt"],
            },
        }
