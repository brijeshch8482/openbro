"""Playbook base class — a pre-built workflow that handles one intent."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbro.tools.registry import ToolRegistry


@dataclass
class PlaybookContext:
    """Everything a playbook needs to run.

    Wraps the agent's tool registry plus the original user input so the
    playbook can call tools without dragging in the whole Agent object.
    Also carries the detected captures from regex matching so the
    playbook knows e.g. WHICH app the user wants to close.
    """

    user_input: str
    tool_registry: ToolRegistry
    captures: dict[str, str] = field(default_factory=dict)
    language: str = "hinglish"


@dataclass
class PlaybookMatch:
    """Result of trying to match a query to a playbook."""

    playbook: Playbook
    confidence: float  # 0.0 - 1.0
    captures: dict[str, str]  # named regex groups extracted from the query


class Playbook(ABC):
    """A pre-built workflow that handles one user intent without the LLM.

    Subclasses must:
      - set `name` (snake_case identifier)
      - set `description` (one-line, surfaces in /playbooks command)
      - provide `triggers` (regex patterns OR keyword sets)
      - implement `execute(context) -> str` (the actual workflow)

    Optionally override `match(query) -> float` for custom scoring beyond
    the default regex/keyword check.
    """

    name: str = ""
    description: str = ""
    # List of (regex, confidence_multiplier) tuples. Highest match wins.
    # Named groups in the regex become `context.captures`. Use the
    # multiplier to express "this pattern is more confident than that one"
    # (e.g. exact phrase = 1.0, fuzzy keyword = 0.6).
    triggers: list[tuple[re.Pattern[str], float]] = []
    # Bare keyword/phrase fallbacks — any of these as a SUBSTRING in the
    # lowercased query yields a 0.5 confidence match. Lets the playbook
    # fire on casual phrasing without writing 20 regexes.
    keywords: list[str] = []
    # If True, this playbook runs but ALSO lets the LLM see the output
    # (for cases where the user wants a richer follow-up). Default False:
    # playbook output IS the response, no LLM call.
    pass_through_to_llm: bool = False

    def match(self, query: str) -> PlaybookMatch | None:
        """Return the best match for `query`, or None if no match."""
        if not query or not query.strip():
            return None
        q = query.strip()
        q_lower = q.lower()

        best: PlaybookMatch | None = None
        for pattern, weight in self.triggers:
            m = pattern.search(q)
            if m:
                conf = min(1.0, weight)
                captures = {k: v for k, v in m.groupdict().items() if v is not None}
                if best is None or conf > best.confidence:
                    best = PlaybookMatch(
                        playbook=self,
                        confidence=conf,
                        captures=captures,
                    )

        if best is None:
            for kw in self.keywords:
                if kw.lower() in q_lower:
                    return PlaybookMatch(
                        playbook=self,
                        confidence=0.5,
                        captures={},
                    )
        return best

    @abstractmethod
    def execute(self, context: PlaybookContext) -> str:
        """Run the workflow and return the final user-facing response."""
        ...

    def info(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": len(self.triggers),
            "keywords": len(self.keywords),
        }


# ─── Helpers playbooks use to format output ────────────────────────────


def render_table(rows: list[dict[str, str]], columns: list[str] | None = None) -> str:
    """Render a list of dicts as a markdown table (Rich renders it nicely).

    Picks columns from the first row's keys if not specified. Falsy/missing
    values render as empty strings. Used by file_search / process_check /
    other multi-item playbook outputs so the response always lands as a
    structured table instead of a paragraph the model might mangle.
    """
    if not rows:
        return ""
    cols = columns or list(rows[0].keys())
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body_lines = []
    for r in rows:
        body_lines.append("| " + " | ".join(str(r.get(c, "") or "") for c in cols) + " |")
    return "\n".join([header, sep, *body_lines])


def render_status_lines(items: list[tuple[str, str]]) -> str:
    """Render a list of (label, value) pairs as bullet points."""
    return "\n".join(f"- **{label}**: {value}" for label, value in items)
