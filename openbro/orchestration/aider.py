"""Aider CLI adapter (open-source git-aware coding assistant).

Uses `aider --message <task> --yes --no-stream --no-pretty` for
non-interactive runs. Aider auto-commits to git after edits.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from openbro.orchestration.base import CliAgent, CliAgentResult

AIDER_FILE_PATTERNS = [
    re.compile(r"^Applied edit to (.+)$"),
    re.compile(r"^Added (.+) to the chat$"),
    re.compile(r"^Wrote (.+)$"),
]
AIDER_COST_PATTERN = re.compile(r"\$\s*([0-9]+\.[0-9]+)")


class AiderAgent(CliAgent):
    name = "Aider"
    binary = "aider"
    install_url = "https://aider.chat"
    install_cmd = "pip install aider-chat"
    description = (
        "Aider — open-source CLI coding agent that auto-commits to git. "
        "Great when you want every change tracked as a git commit."
    )

    def build_command(self, task, cwd, max_cost_usd):
        cmd = [
            self.binary,
            "--message",
            task,
            "--yes",
            "--no-stream",
            "--no-pretty",
            "--no-show-model-warnings",
        ]
        if max_cost_usd:
            # aider doesn't have a hard $ cap, but it has --map-tokens etc.
            # We skip; rely on OpenBro's cli-level daily budget.
            pass
        return cmd

    def parse_stream(self, stdout_lines: Iterable[str], on_event) -> CliAgentResult:
        text_parts: list[str] = []
        files: set[str] = set()
        cost_usd = 0.0
        raw: list[str] = []

        for line in stdout_lines:
            stripped = line.rstrip()
            if not stripped:
                continue
            raw.append(stripped)
            text_parts.append(stripped)
            on_event("cli_agent", stripped[:160])

            for pat in AIDER_FILE_PATTERNS:
                m = pat.search(stripped)
                if m:
                    files.add(m.group(1).strip())

            if "Cost" in stripped or "Tokens:" in stripped:
                cm = AIDER_COST_PATTERN.search(stripped)
                if cm:
                    try:
                        cost_usd = max(cost_usd, float(cm.group(1)))
                    except ValueError:
                        pass

        return CliAgentResult(
            success=True,
            summary="\n".join(text_parts[-30:]).strip(),
            cost_usd=cost_usd,
            files_touched=list(files),
            tools_used=[],
            raw_output="\n".join(raw),
        )
