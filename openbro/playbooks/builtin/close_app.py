"""CloseAppPlaybook — 'close my browser' / 'X band karo' without LLM.

Routes directly to app.close. Group aliases like 'browser' / 'browsers'
get handled by the (already-fixed) AppTool group resolution.
"""

from __future__ import annotations

import re

from openbro.playbooks.base import Playbook, PlaybookContext


class CloseAppPlaybook(Playbook):
    name = "close_app"
    description = "Close an app (or a category like 'browser')."
    triggers = [
        # 'close my browser' / 'close chrome' / 'close X.exe'
        (
            re.compile(
                r"\bclose\s+(my\s+|the\s+)?(?P<target>[\w \-.+]+?)\s*$",
                re.IGNORECASE,
            ),
            1.0,
        ),
        # 'kill chrome' / 'kill X process'
        (
            re.compile(
                r"\bkill\s+(?P<target>[\w \-.+]+?)\s*(process)?\s*$",
                re.IGNORECASE,
            ),
            1.0,
        ),
        # 'X band kar' / 'X bandh karo' (Hinglish)
        (
            re.compile(
                r"\b(?P<target>[\w \-.+]+?)\s+(band|bandh)\s+(kar|karo|kr|kro|de)\b",
                re.IGNORECASE,
            ),
            1.0,
        ),
        # 'mera browser band kr'
        (
            re.compile(
                r"\bmera\s+(?P<target>[\w \-.+]+?)\s+(band|bandh)\s+(kar|karo|kr|kro|de)\b",
                re.IGNORECASE,
            ),
            1.0,
        ),
        # 'exit X' / 'quit X'
        (
            re.compile(
                r"\b(exit|quit)\s+(?P<target>[\w \-.+]+?)\s*$",
                re.IGNORECASE,
            ),
            0.85,
        ),
    ]
    keywords: list[str] = []

    def execute(self, context: PlaybookContext) -> str:
        target = (context.captures.get("target") or "").strip().lower()
        # Clean any trailing noise the regex slurped.
        target = re.sub(r"\b(app|application|window|tab)\b", "", target).strip()
        if not target:
            return "_What should I close? E.g. `close brave` or `close my browser`._"

        tool = context.tool_registry.get_tool("app")
        if tool is None:
            return "_app tool not available._"

        result = tool.run(action="close", app_name=target)
        # Decorate slightly so it looks intentional, not raw.
        if result.startswith("Closed:") or "Closed:" in result.split("\n")[0]:
            return f"✓ {result}"
        if "not found" in result.lower() or "No matching" in result:
            return f"⚠ Nothing to close — {result}"
        if result.startswith("Could not close") or "Error" in result:
            return f"✗ {result}"
        return result
