"""PlannerPlaybook — inject 'plan before acting' for complex queries.

Captured ask (2026-05-30): 'bhai openbro kyo nhi question ko break kr
ke..answer ka ek path bna kr step by step krta?? jaise claude krta hai?'

OpenBro already has `decompose` for explicitly compound queries
('X aur Y', 'X then Y', numbered lists) — those run as separate
turns via TaskList. But many complex queries don't have those
conjunctions yet still need a multi-step plan:

  - 'tell me how to set up django auth from scratch'
  - 'compare these two files and explain which one is better'
  - 'step by step batao kya kya kar sakte hain MTP kiosk mode me'
  - 'ek ek karke explain kar yeh code'

For those, this playbook injects a `[TRANSIENT_PLAN]` system message
telling the LLM:

  1. FIRST emit a 1-5 step plan as a markdown numbered list
  2. THEN start executing step 1
  3. After each tool result, restate which step you just finished
     and which step is next

That gives the user a visible Claude-Code-style 'plan + ticking
todo' without us having to make a separate planner LLM call (which
would double tokens on every complex turn). The plan emerges in the
LLM's normal step-1 response.

The playbook is `pass_through_to_llm=True` — it doesn't return a
final answer, it just augments the system context for the next LLM
call. The `[TRANSIENT_PLAN]` marker is pruned by the agent after the
final answer (same mechanism that prunes `[TRANSIENT_RESEARCH]`).
"""

from __future__ import annotations

import re

from openbro.playbooks.base import Playbook, PlaybookContext

# Explicit ask patterns — user literally says 'plan' / 'step by step' /
# 'break it down'. These always fire the planner.
_EXPLICIT_PLAN_PATTERNS = [
    re.compile(r"\b(step[- ]by[- ]step|step wise|stepwise)\b", re.IGNORECASE),
    re.compile(r"\b(break (it|this|that) down|break (it|this|that) into)\b", re.IGNORECASE),
    re.compile(r"\b(make|build|create|give me) a plan\b", re.IGNORECASE),
    re.compile(r"\bplan (banao|bana de|bna do|bna)\b", re.IGNORECASE),
    re.compile(r"\bek\s+ek\s+kar(ke|ke|)\b", re.IGNORECASE),
    re.compile(r"\bek-ek karke\b", re.IGNORECASE),
    re.compile(
        r"\b(detail|detailed|deeply|thoroughly) (explain|batao|bata|describe)\b", re.IGNORECASE
    ),
    re.compile(r"\b(walk me through|guide me through)\b", re.IGNORECASE),
    re.compile(r"\bcompare\b.+\b(and|aur)\b.+\b(explain|tell|batao|bata)\b", re.IGNORECASE),
]

# Implicit complexity signals — only fire when MULTIPLE signals hit so
# we don't planner-ize every long question.
_MULTI_ACTION_VERBS = re.compile(
    r"\b(check|run|test|verify|explain|show|list|find|fetch|read|"
    r"analyze|compare|build|create|install|configure|setup|deploy|"
    r"investigate|debug|fix|migrate|implement|refactor)\b",
    re.IGNORECASE,
)


def _looks_complex(query: str) -> bool:
    """Returns True when the query benefits from a planning hint.

    Two paths:
      - Explicit plan request (_EXPLICIT_PLAN_PATTERNS) — always fire.
      - Implicit complexity — fires when ALL of:
          a) > 80 chars (filters quick questions)
          b) 3+ distinct action verbs (multi-step solution implied)
          c) has a question mark or imperative shape
    """
    q = (query or "").strip()
    if len(q) < 20:
        return False

    for pat in _EXPLICIT_PLAN_PATTERNS:
        if pat.search(q):
            return True

    # Implicit path: long query + multiple action verbs.
    if len(q) < 80:
        return False
    verbs = _MULTI_ACTION_VERBS.findall(q.lower())
    distinct = {v.lower() for v in verbs}
    if len(distinct) < 3:
        return False
    has_imperative = "?" in q or any(
        q.lower().startswith(p)
        for p in ("how ", "what ", "why ", "show ", "tell ", "explain ", "list ", "find ")
    )
    return has_imperative


_PLANNER_INSTRUCTION = (
    "[TRANSIENT_PLAN] This query benefits from explicit planning. "
    "BEFORE making any tool call or writing the final answer, do this:\n"
    "\n"
    "  1. Emit a numbered plan (1-5 steps max) as a markdown list. "
    "Each step should be a single concrete action: a tool to call, a "
    "file to read, a comparison to make, an explanation to write.\n"
    "  2. After the plan, write `Starting step 1.` and immediately "
    "execute it (tool call or specific reasoning).\n"
    "  3. After each tool result, write `Step N done.` then the next "
    "concrete action, OR if all steps are done, write the final "
    "synthesised answer.\n"
    "\n"
    "Rules for the plan:\n"
    "  • Don't pad — 1 step is fine if that's all the query needs.\n"
    "  • Don't promise steps you won't execute.\n"
    "  • Each step must be checkable: 'read file X', 'run shell Y', "
    "'compare A and B', NOT 'understand the problem'.\n"
    "  • If you genuinely don't need a plan (1-tool answer), skip the "
    "list and just answer.\n"
)


class PlannerPlaybook(Playbook):
    """Inject a planning hint into the LLM context for complex queries.

    Returns `[TRANSIENT_PLAN]` as the response so the agent treats it
    as pass-through context (NOT a final answer). The marker is pruned
    by the agent after the final synthesised answer arrives.
    """

    name = "planner"
    description = "Inject 'plan before acting' hint for multi-step queries"
    pass_through_to_llm = True

    def match(self, query: str):  # type: ignore[override]
        if not _looks_complex(query):
            return None
        from openbro.playbooks.base import PlaybookMatch

        return PlaybookMatch(playbook=self, confidence=0.65, captures={})

    def execute(self, context: PlaybookContext) -> str:
        return _PLANNER_INSTRUCTION
