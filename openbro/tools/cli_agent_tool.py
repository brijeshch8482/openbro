"""Unified CLI agent tool — delegate tasks to claude / codex / aider / gemini.

The agent picks `cli_agent` whenever the user asks to delegate work to an
external AI CLI ("Claude se bolo X kar", "Codex se ye refactor karwa de",
"Aider se git commit ke saath ye change kar"). Each adapter handles its
own command syntax + output parsing.

Cost guard: per-call cap + daily budget tracked per agent in
~/.openbro/cli_agent_spend.json.
"""

from __future__ import annotations

import os

from openbro.core.activity import get_bus
from openbro.orchestration import available_agents, get_agent
from openbro.orchestration.base import record_spend, today_spend
from openbro.tools.base import BaseTool, RiskLevel
from openbro.utils.config import load_config

DEFAULT_PER_CALL_USD = 1.00
DEFAULT_DAILY_USD = 10.00
DEFAULT_TIMEOUT = 600


class CliAgentTool(BaseTool):
    name = "cli_agent"
    description = (
        "Delegate a coding/automation task to an external AI CLI agent "
        "(Claude Code, Codex, Aider, Gemini). Use this when the user says "
        "'Claude se bolo X kar', 'Codex se ye karwa', 'Aider se commit kar de', "
        "or for any task that needs another AI's help to read/edit files. "
        "Returns the CLI's summary plus list of files touched."
    )
    risk = RiskLevel.MODERATE

    def schema(self) -> dict:
        agents = list(available_agents().keys()) or list(get_agent("claude") and ["claude"]) or []
        # If no agent is installed, still expose the tool so it can return a useful
        # 'install X' error rather than disappearing from the LLM's view.
        if not agents:
            agents = ["claude", "codex", "aider", "gemini"]
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "enum": agents,
                        "description": (
                            "Which CLI to delegate to. Pick based on user's request: "
                            "'claude' for careful multi-file work, 'codex' for fast "
                            "single-file edits, 'aider' when git commits are wanted, "
                            "'gemini' for large-context summarisation."
                        ),
                    },
                    "task": {
                        "type": "string",
                        "description": (
                            "What the CLI should do — full instructions including "
                            "paths, files, constraints. Like briefing a senior dev."
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Working directory (absolute path). Defaults to current dir."
                        ),
                    },
                    "max_cost_usd": {
                        "type": "number",
                        "description": (
                            "Per-call USD budget cap. Default from config "
                            "safety.cli_agent.max_cost_per_call_usd."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max wall-clock seconds (default 600).",
                    },
                },
                "required": ["agent", "task"],
            },
        }

    def run(self, **kwargs) -> str:
        agent_name = (kwargs.get("agent") or "").lower().strip()
        task = (kwargs.get("task") or "").strip()
        if not agent_name or not task:
            return "Both 'agent' and 'task' are required."

        agent = get_agent(agent_name)
        if not agent:
            return (
                f"Unknown agent '{agent_name}'. Available: "
                f"{', '.join(available_agents().keys()) or 'none installed'}. "
                f"Known adapters: claude, codex, aider, gemini."
            )

        if not agent.is_installed():
            return agent.install_hint()

        cfg = load_config()
        cli_cfg = cfg.get("safety", {}).get("cli_agent", {}) or {}
        max_per_call = float(
            kwargs.get("max_cost_usd") or cli_cfg.get("max_cost_per_call_usd", DEFAULT_PER_CALL_USD)
        )
        daily_budget = float(cli_cfg.get("daily_budget_usd", DEFAULT_DAILY_USD))
        timeout = int(kwargs.get("timeout") or cli_cfg.get("timeout_seconds", DEFAULT_TIMEOUT))
        cwd = kwargs.get("cwd") or os.getcwd()

        today = today_spend(agent_name)
        if today >= daily_budget:
            return (
                f"Daily {agent.name} budget hit (${today:.2f}/${daily_budget:.2f}). "
                f"Reset tomorrow or raise safety.cli_agent.daily_budget_usd."
            )
        cap = min(max_per_call, max(daily_budget - today, 0.0)) if daily_budget else max_per_call

        get_bus().emit(
            "cli_agent",
            f"{agent.name}: launching",
            agent=agent_name,
            cwd=cwd,
            cap_usd=cap,
        )

        try:
            result = agent.run(task=task, cwd=cwd, max_cost_usd=cap, timeout=timeout)
        except FileNotFoundError:
            return f"Could not exec {agent.binary}."
        except Exception as e:
            return f"{agent.name} error: {e}"

        today_total, _ = record_spend(agent_name, result.cost_usd)
        get_bus().emit(
            "cli_agent",
            f"{agent.name}: done · ${result.cost_usd:.3f} · {len(result.files_touched)} files",
            agent=agent_name,
            cost_usd=result.cost_usd,
        )
        return result.format(agent.name, today_total)
