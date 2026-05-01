"""Gemini CLI adapter (Google's gemini-cli).

Uses `gemini -p <task> --yolo` (or whatever flag is available) for
non-interactive runs. Output is plain text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from openbro.orchestration.base import CliAgent, CliAgentResult

GEMINI_FILE_PATTERN = re.compile(
    r"(?:writing|wrote|updated|created|modified)\s+([^\s]+\.[a-z0-9]+)",
    re.IGNORECASE,
)


class GeminiAgent(CliAgent):
    name = "Gemini"
    binary = "gemini"
    install_url = "https://github.com/google-gemini/gemini-cli"
    install_cmd = "npm install -g @google/gemini-cli"
    description = (
        "Google's Gemini CLI — fast, large context window, good for documentation "
        "tasks, summaries, and quick edits."
    )

    def build_command(self, task, cwd, max_cost_usd):
        # gemini-cli supports `-p` for prompt; --yolo accepts edits non-interactively.
        return [self.binary, "-p", task, "--yolo"]

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
            for m in GEMINI_FILE_PATTERN.finditer(stripped):
                files.add(m.group(1))

        return CliAgentResult(
            success=True,
            summary="\n".join(text_parts[-30:]).strip(),
            cost_usd=0.0,
            files_touched=list(files),
            tools_used=[],
            raw_output="\n".join(raw),
        )
