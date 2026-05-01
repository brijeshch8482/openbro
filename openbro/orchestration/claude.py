"""Claude Code adapter (the `claude` CLI from Anthropic).

Uses --print --output-format stream-json for live JSON event streaming.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from openbro.orchestration.base import CliAgent, CliAgentResult


class ClaudeAgent(CliAgent):
    name = "Claude Code"
    binary = "claude"
    install_url = "https://docs.claude.com/en/docs/claude-code/quickstart"
    install_cmd = "npm install -g @anthropic-ai/claude-code"
    description = (
        "Anthropic's Claude Code CLI — best for multi-file refactors, codebase-wide "
        "changes, careful code reviews, and following complex instructions."
    )

    def build_command(self, task, cwd, max_cost_usd):
        cmd = [
            self.binary,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            task,
        ]
        if max_cost_usd:
            cmd.extend(["--max-budget-usd", f"{max_cost_usd:.2f}"])
        return cmd

    def parse_stream(self, stdout_lines: Iterable[str], on_event) -> CliAgentResult:
        text_parts: list[str] = []
        files: set[str] = set()
        tools: list[str] = []
        cost_usd = 0.0
        raw: list[str] = []

        for line in stdout_lines:
            line = line.strip()
            if not line:
                continue
            raw.append(line)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            ev_type = ev.get("type")
            if ev_type == "system":
                sub = ev.get("subtype", "")
                if sub:
                    on_event("cli_agent", f"system: {sub}")
            elif ev_type == "assistant":
                msg = ev.get("message", {}) or {}
                for block in msg.get("content", []) or []:
                    btype = block.get("type")
                    if btype == "text":
                        txt = block.get("text", "")
                        if txt:
                            text_parts.append(txt)
                            on_event("cli_agent", txt[:160])
                    elif btype == "tool_use":
                        tname = block.get("name", "?")
                        tools.append(tname)
                        inp = block.get("input", {}) or {}
                        target = (
                            inp.get("file_path")
                            or inp.get("path")
                            or inp.get("command")
                            or inp.get("pattern")
                            or ""
                        )
                        if tname in ("Edit", "Write", "MultiEdit") and inp.get("file_path"):
                            files.add(inp["file_path"])
                        on_event("cli_agent", f"→ {tname}: {str(target)[:120]}")
            elif ev_type == "result":
                cost_usd = float(ev.get("total_cost_usd") or ev.get("cost_usd") or 0.0)
                txt = ev.get("result") or ev.get("text") or ""
                if txt and not text_parts:
                    text_parts.append(txt)

        return CliAgentResult(
            success=True,
            summary="\n".join(text_parts).strip(),
            cost_usd=cost_usd,
            files_touched=list(files),
            tools_used=tools,
            raw_output="\n".join(raw),
        )
