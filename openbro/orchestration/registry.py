"""Registry of all known CLI agent adapters."""

from openbro.orchestration.aider import AiderAgent
from openbro.orchestration.base import CliAgent
from openbro.orchestration.claude import ClaudeAgent
from openbro.orchestration.codex import CodexAgent
from openbro.orchestration.gemini import GeminiAgent

ALL_AGENTS: dict[str, CliAgent] = {
    "claude": ClaudeAgent(),
    "codex": CodexAgent(),
    "aider": AiderAgent(),
    "gemini": GeminiAgent(),
}


def available_agents() -> dict[str, CliAgent]:
    """Return only the adapters whose CLI binary is on PATH."""
    return {n: a for n, a in ALL_AGENTS.items() if a.is_installed()}


def get_agent(name: str) -> CliAgent | None:
    return ALL_AGENTS.get(name.lower())
