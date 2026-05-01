"""Codex CLI adapter (OpenAI Codex CLI).

Uses `codex exec <task>` for non-interactive runs. Output is plain text;
we capture the full stdout and detect file edits from the textual log.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from openbro.orchestration.base import CliAgent, CliAgentResult

# Heuristic patterns Codex prints when editing files.
FILE_EDIT_PATTERNS = [
    re.compile(r"(?:^|\s)(?:writing|wrote|updated|created|edited|modified)\s+([^\s]+\.[a-z0-9]+)"),
    re.compile(r"^\s*[+\-]{3}\s+([^\s]+\.[a-z0-9]+)", re.MULTILINE),  # diff headers
]


class CodexAgent(CliAgent):
    name = "Codex"
    binary = "codex"
    install_url = "https://github.com/openai/codex"
    install_cmd = "npm install -g @openai/codex"
    description = (
        "OpenAI Codex CLI — fast pair-programmer style edits. Good for quick "
        "single-file changes and Q&A about a codebase."
    )

    def build_command(self, task, cwd, max_cost_usd):
        # codex exec runs non-interactively; --quiet to keep output clean
        return [self.binary, "exec", "--quiet", task]

    def parse_stream(self, stdout_lines: Iterable[str], on_event) -> CliAgentResult:
        text_parts: list[str] = []
        files: set[str] = set()
        raw: list[str] = []

        for line in stdout_lines:
            stripped = line.rstrip()
            if not stripped:
                continue
            raw.append(stripped)
            text_parts.append(stripped)
            on_event("cli_agent", stripped[:160])
            for pat in FILE_EDIT_PATTERNS:
                for m in pat.finditer(stripped):
                    files.add(m.group(1))

        return CliAgentResult(
            success=True,
            summary="\n".join(text_parts[-30:]).strip(),  # last 30 lines as summary
            cost_usd=0.0,  # codex doesn't report cost
            files_touched=list(files),
            tools_used=[],
            raw_output="\n".join(raw),
        )
