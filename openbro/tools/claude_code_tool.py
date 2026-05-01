"""Claude Code orchestration tool.

Lets the agent delegate complex coding tasks to Claude Code (the `claude` CLI).
Spawns claude as a subprocess in --print mode with stream-json output, parses
each event, and emits live progress to the activity bus.

Cost guard:
- Per-call hard limit (--max-budget-usd) from config
- Daily budget tracked in ~/.openbro/claude_spend.json

Example user flow:
  You > Claude se bolo openbro me ek weather tool add kar
  → LLM picks claude_code tool
  → Permission prompt (it's MODERATE)
  → Subprocess spawns, live events flood the activity panel
  → Final summary returned to user
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

from openbro.core.activity import get_bus
from openbro.tools.base import BaseTool, RiskLevel
from openbro.utils.config import load_config
from openbro.utils.storage import get_storage_paths

DEFAULT_PER_CALL_USD = 1.00
DEFAULT_DAILY_USD = 10.00
DEFAULT_TIMEOUT = 600  # 10 min


def _spend_path() -> Path:
    base = Path(get_storage_paths().get("base", Path.home() / ".openbro"))
    return base / "claude_spend.json"


def _load_spend() -> dict:
    p = _spend_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _record_spend(usd: float) -> tuple[float, float]:
    """Add usd to today's spend; return (today_total, all_time_total)."""
    spend = _load_spend()
    today = date.today().isoformat()
    spend.setdefault("daily", {})
    spend["daily"][today] = spend["daily"].get(today, 0.0) + usd
    spend["total"] = spend.get("total", 0.0) + usd
    try:
        _spend_path().parent.mkdir(parents=True, exist_ok=True)
        _spend_path().write_text(json.dumps(spend, indent=2))
    except Exception:
        pass
    return spend["daily"][today], spend["total"]


def _today_spend() -> float:
    spend = _load_spend()
    return spend.get("daily", {}).get(date.today().isoformat(), 0.0)


class ClaudeCodeTool(BaseTool):
    name = "claude_code"
    description = (
        "Delegate a coding/automation task to Claude Code (the `claude` CLI). "
        "Use this when the user asks you to 'tell Claude to do X' or for tasks "
        "that need code edits, file analysis, or multi-step engineering work. "
        "Returns Claude's final summary plus a list of files changed."
    )
    risk = RiskLevel.MODERATE

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "What Claude should do — written as you'd say it to a "
                            "senior engineer. Include enough context (paths, files, "
                            "constraints). Example: 'In D:/OpenBro, add a weather "
                            "tool under openbro/tools/ using Open-Meteo API. "
                            "Risk SAFE. Write a unit test.'"
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Working directory for Claude. Default: OpenBro repo root. "
                            "Use absolute paths."
                        ),
                    },
                    "max_cost_usd": {
                        "type": "number",
                        "description": (
                            "Per-call budget cap (USD). Default from config "
                            "safety.claude_code.max_cost_per_call_usd."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max wall-clock seconds (default 600).",
                    },
                },
                "required": ["task"],
            },
        }

    def run(self, **kwargs) -> str:
        task = (kwargs.get("task") or "").strip()
        if not task:
            return "task is required."

        claude_bin = shutil.which("claude")
        if not claude_bin:
            return (
                "Claude Code CLI not found. Install it: "
                "https://docs.claude.com/en/docs/claude-code/quickstart "
                "(npm install -g @anthropic-ai/claude-code)."
            )

        cfg = load_config()
        cc_cfg = cfg.get("safety", {}).get("claude_code", {}) or {}
        max_per_call = float(
            kwargs.get("max_cost_usd") or cc_cfg.get("max_cost_per_call_usd", DEFAULT_PER_CALL_USD)
        )
        daily_budget = float(cc_cfg.get("daily_budget_usd", DEFAULT_DAILY_USD))
        timeout = int(kwargs.get("timeout") or cc_cfg.get("timeout_seconds", DEFAULT_TIMEOUT))
        cwd = kwargs.get("cwd") or os.getcwd()

        today = _today_spend()
        if today >= daily_budget:
            return (
                f"Daily Claude Code budget hit (${today:.2f}/${daily_budget:.2f}). "
                f"Reset tomorrow or raise safety.claude_code.daily_budget_usd."
            )
        remaining = daily_budget - today
        effective_cap = min(max_per_call, remaining)

        bus = get_bus()
        bus.emit(
            "claude",
            f"start: {task[:120]}",
            cwd=cwd,
            cap_usd=effective_cap,
        )

        cmd = [
            claude_bin,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-budget-usd",
            f"{effective_cap:.2f}",
            "--permission-mode",
            "acceptEdits",  # auto-accept file edits since OpenBro's gate already approved
            task,
        ]

        try:
            return self._run_subprocess(cmd, cwd, timeout)
        except FileNotFoundError:
            return "Could not exec claude CLI."
        except Exception as e:
            return f"claude_code error: {e}"

    def _run_subprocess(self, cmd: list[str], cwd: str, timeout: int) -> str:
        bus = get_bus()
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        final_text_parts: list[str] = []
        files_touched: set[str] = set()
        cost_usd = 0.0
        tool_calls: list[str] = []

        def _drain_stderr():
            assert proc.stderr is not None
            for line in proc.stderr:
                if line.strip():
                    bus.emit("claude", f"stderr: {line.strip()[:200]}")

        t = threading.Thread(target=_drain_stderr, daemon=True)
        t.start()

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._process_event(ev, final_text_parts, files_touched, tool_calls, bus)
                if ev.get("type") == "result":
                    cost_usd = float(ev.get("total_cost_usd") or ev.get("cost_usd") or 0.0)

            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return f"Claude Code timed out after {timeout}s."

        today_total, _ = _record_spend(cost_usd)
        bus.emit(
            "claude",
            f"done: {len(files_touched)} files, ${cost_usd:.3f}",
            cost_usd=cost_usd,
            files=list(files_touched),
        )

        summary = "\n".join(final_text_parts).strip() or "(no text response)"
        files_str = "\n  ".join(sorted(files_touched)) if files_touched else "(none)"
        tools_str = ", ".join(tool_calls) if tool_calls else "(none)"
        return (
            f"Claude Code finished.\n"
            f"Cost: ${cost_usd:.3f} (today: ${today_total:.2f})\n"
            f"Tools used: {tools_str}\n"
            f"Files touched:\n  {files_str}\n\n"
            f"Summary:\n{summary}"
        )

    @staticmethod
    def _process_event(
        ev: dict,
        text_parts: list[str],
        files: set[str],
        tools: list[str],
        bus,
    ) -> None:
        ev_type = ev.get("type")
        if ev_type == "system":
            sub = ev.get("subtype", "")
            if sub:
                bus.emit("claude", f"system: {sub}")
            return

        if ev_type == "assistant":
            msg = ev.get("message", {}) or {}
            for block in msg.get("content", []) or []:
                btype = block.get("type")
                if btype == "text":
                    txt = block.get("text", "")
                    if txt:
                        text_parts.append(txt)
                        bus.emit("claude", txt[:160])
                elif btype == "tool_use":
                    tool_name = block.get("name", "?")
                    tools.append(tool_name)
                    inp = block.get("input", {}) or {}
                    target = (
                        inp.get("file_path")
                        or inp.get("path")
                        or inp.get("command")
                        or inp.get("pattern")
                        or ""
                    )
                    if tool_name in ("Edit", "Write", "MultiEdit") and inp.get("file_path"):
                        files.add(inp["file_path"])
                    bus.emit("claude", f"→ {tool_name}: {str(target)[:120]}")
            return

        if ev_type == "user":
            # tool results coming back to claude
            return

        if ev_type == "result":
            sub = ev.get("subtype", "")
            txt = ev.get("result") or ev.get("text") or ""
            if txt and not text_parts:
                text_parts.append(txt)
            bus.emit("claude", f"result: {sub}")
            return


_test_mode = False


def _set_test_mode(on: bool) -> None:
    """Used by tests to bypass shutil.which check."""
    global _test_mode
    _test_mode = on
    sys.modules[__name__]._test_mode = on
