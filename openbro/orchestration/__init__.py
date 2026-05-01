"""CLI agent orchestration - run external AI CLIs (claude, codex, aider, gemini, ...).

Each adapter knows how to:
- check if its CLI is installed
- build a non-interactive command from a free-text task
- parse the CLI's stdout stream into structured events (text, tool calls,
  files touched, cost) and emit them to the OpenBro activity bus

The unified `cli_agent` tool exposes all installed adapters to the LLM under
one interface — it decides which CLI to delegate to based on the user's request.
"""

from openbro.orchestration.base import CliAgent, CliAgentResult
from openbro.orchestration.registry import ALL_AGENTS, available_agents, get_agent

__all__ = [
    "ALL_AGENTS",
    "CliAgent",
    "CliAgentResult",
    "available_agents",
    "get_agent",
]
