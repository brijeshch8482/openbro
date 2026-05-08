"""Multi-role sub-agent — Planner / Executor / Verifier internally.

For complex prompts, OpenBro splits the work across three lightweight LLM
calls instead of one big one. This is the 'agent' loop that turns plain
chat replies into actual multi-step task completion.

Flow:
    Planner   (cheap LLM)  → JSON list of steps
    Executor  (main LLM)   → run each step, optionally with tools
    Verifier  (cheap LLM)  → did the result match the plan? quick yes/no

When it's overkill (greeting, simple Q&A) we skip the planner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from openbro.core.activity import get_bus
from openbro.llm.base import Message

# Heuristic: complex enough to need a plan?
COMPLEX_PATTERNS = re.compile(
    r"\b(and|then|after|step|first|second|third|aur|fir|ke baad|"
    r"refactor|create|generate|build|integrate|migrate|setup)\b",
    re.IGNORECASE,
)

PLANNER_SYSTEM = """You are a planning agent. Break the user's task into 3-7
concrete steps. Output ONLY a JSON array of strings, nothing else.

Example:
User: "fix the typo in main.py and run the tests"
Output: ["read main.py", "find typo", "edit main.py to fix typo",
         "run pytest", "report results"]"""

VERIFIER_SYSTEM = """You are a verifier. Given a plan and the executor's
output, answer ONLY 'yes' or 'no' followed by a short reason.

Format: 'yes: <reason>' or 'no: <reason>'"""


@dataclass
class MultiRoleResult:
    steps: list[str] = field(default_factory=list)
    output: str = ""
    verified: bool = True
    verifier_note: str = ""
    used_planner: bool = False
    used_verifier: bool = False


def needs_planning(prompt: str) -> bool:
    """Return True if the prompt has multi-step/complex structure."""
    if len(prompt) < 30:
        return False
    if "?" in prompt and len(prompt) < 80:
        return False  # likely a simple question
    matches = COMPLEX_PATTERNS.findall(prompt)
    return len(matches) >= 2 or len(prompt) > 200


def plan(llm, prompt: str) -> list[str]:
    """Use the LLM to break a task into steps. Returns [] if planning fails."""
    try:
        resp = llm.chat(
            [
                Message(role="system", content=PLANNER_SYSTEM),
                Message(role="user", content=prompt),
            ],
            tools=None,
        )
        raw = (resp.content or "").strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        steps = json.loads(raw)
        if isinstance(steps, list):
            return [str(s) for s in steps if s]
    except (json.JSONDecodeError, AttributeError, Exception):
        pass
    return []


def verify(llm, plan_steps: list[str], output: str) -> tuple[bool, str]:
    """Cheap verifier check: 'did this output match the plan?'"""
    if not plan_steps or not output:
        return True, "no plan to verify against"
    summary = f"Plan was: {json.dumps(plan_steps)}\n\nExecutor output:\n{output[:500]}"
    try:
        resp = llm.chat(
            [
                Message(role="system", content=VERIFIER_SYSTEM),
                Message(role="user", content=summary),
            ],
            tools=None,
        )
        raw = (resp.content or "").strip().lower()
        ok = raw.startswith("yes")
        note = raw[3:].lstrip(":").strip()
        return ok, note
    except Exception as e:
        return True, f"verifier error: {e}"


def run_multi_role(
    prompt: str,
    main_llm,
    planner_llm=None,
    verifier_llm=None,
    executor_fn=None,
) -> MultiRoleResult:
    """Run the full multi-role pipeline.

    main_llm     — used for the actual reasoning step
    planner_llm  — cheap/small LLM for planning (defaults to main_llm)
    verifier_llm — cheap/small LLM for verification (defaults to main_llm)
    executor_fn  — function (prompt) -> str that does the heavy lifting; if
                   None, we just call main_llm.chat() directly
    """
    bus = get_bus()
    result = MultiRoleResult()

    if needs_planning(prompt):
        plan_llm = planner_llm or main_llm
        bus.emit("brain", "planner: breaking task into steps")
        result.steps = plan(plan_llm, prompt)
        result.used_planner = bool(result.steps)
        if result.steps:
            bus.emit("brain", f"plan: {len(result.steps)} steps")

    # Executor
    if executor_fn:
        result.output = executor_fn(prompt)
    else:
        try:
            resp = main_llm.chat([Message(role="user", content=prompt)], tools=None)
            result.output = resp.content or ""
        except Exception as e:
            result.output = f"Executor error: {e}"

    # Verifier (only if we had a plan)
    if result.steps:
        v_llm = verifier_llm or main_llm
        bus.emit("brain", "verifier: checking output")
        ok, note = verify(v_llm, result.steps, result.output)
        result.verified = ok
        result.verifier_note = note
        result.used_verifier = True
        bus.emit("brain", f"verifier: {'pass' if ok else 'fail'} - {note[:80]}")

    return result
