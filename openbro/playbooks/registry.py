"""Playbook registry — picks the best match for a query.

Loads all built-in playbooks at startup and exposes a single `match()`
entry point the Agent calls before falling back to the LLM loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbro.playbooks.base import Playbook, PlaybookMatch

if TYPE_CHECKING:
    pass

# Minimum confidence to fire a playbook automatically. Below this the
# query falls through to the LLM. Tuned empirically — 0.45 lets clear
# keyword matches through (e.g. "kaha hu" → geo_lookup at 0.5) but
# blocks weak coincidental matches.
DEFAULT_MIN_CONFIDENCE = 0.45


class PlaybookRegistry:
    """Holds every Playbook the agent can dispatch to.

    Loaded once per agent. `match()` is hot-path — runs on every user
    turn — so it stays O(N) over playbooks with cheap regex+keyword
    checks. With ~10-20 playbooks the cost is microseconds.
    """

    def __init__(self):
        self._playbooks: list[Playbook] = []
        self._load_builtins()

    def _load_builtins(self) -> None:
        """Import and register every playbook in the builtin/ folder.

        Done lazily so a broken playbook can't crash agent startup —
        each import is wrapped, errors print a warning and the rest
        keep loading.
        """
        from openbro.playbooks.builtin import all_builtin_playbooks

        for pb_cls in all_builtin_playbooks():
            try:
                self._playbooks.append(pb_cls())
            except Exception as e:
                # Don't take the agent down for one bad playbook —
                # log and move on. User can /playbooks to see which
                # loaded.
                import sys

                print(
                    f"[playbook load warning] {pb_cls.__name__}: {e}",
                    file=sys.stderr,
                )

    def register(self, playbook: Playbook) -> None:
        """Hook for adding playbooks at runtime (tests, future plugins)."""
        self._playbooks.append(playbook)

    def match(
        self,
        query: str,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> PlaybookMatch | None:
        """Return the highest-confidence playbook match above threshold.

        Returns None if nothing scores high enough — caller falls back
        to the LLM. Tie-breaker: first registered wins (deterministic).
        """
        best: PlaybookMatch | None = None
        for pb in self._playbooks:
            m = pb.match(query)
            if m is None:
                continue
            if m.confidence < min_confidence:
                continue
            if best is None or m.confidence > best.confidence:
                best = m
        return best

    def list_all(self) -> list[Playbook]:
        return list(self._playbooks)
