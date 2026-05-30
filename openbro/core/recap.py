"""Session recap — Claude Code-style 'where are we' summary.

The user pointed at Claude Code's recap line:

  ※ recap: Goal: improve FogPASS battery backup by making AudioBooster
    lazy and lowering LowVoltageGuard threshold to 3.2V (recovery
    3.4V). All built, signed, installed. Next: unplug charger and run
    discharge test to verify backup time.

…and asked OpenBro to do the same. This module builds that recap from
the agent's chat history:

  1. Walk the last N turns.
  2. Detect goal-setting language ('I want to', 'let's improve',
     'fix the bug in X', 'add Y to Z').
  3. Detect status markers (tool successes, build/test/deploy results
     in tool outputs).
  4. Compose a 'Goal · Status · Next' string and (optionally) let the
     LLM polish it into one paragraph.

The recap is built deterministically when possible so the user gets the
same answer twice on the same history. The LLM polish is opt-in (the
REPL command takes a `--with-llm` flag) so a low-token recap stays
free of model variance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from openbro.llm.base import Message

# Phrases that mark a user turn as 'goal-setting' — i.e. the start of a
# new working theme. The most recent goal-setting turn wins.
_GOAL_PATTERNS = [
    re.compile(r"\b(let'?s|let us|we should|I want to|I need to)\b.*", re.IGNORECASE),
    re.compile(
        r"\b(improve|fix|add|build|make|migrate|deploy|debug|investigate)\b.*", re.IGNORECASE
    ),
    re.compile(r"\b(goal|target|todo|objective)\s*[:\-]\s*", re.IGNORECASE),
]

# Patterns that signal 'something successfully happened' in an assistant
# turn or tool output — used to construct the 'Status' chunk.
_SUCCESS_PATTERNS = [
    re.compile(
        r"\b(done|completed|finished|merged|deployed|pushed|committed|installed|built|fixed)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(all tests pass|tests pass|CI green|✓)\b", re.IGNORECASE),
    re.compile(r"\bsuccess(fully)?\b", re.IGNORECASE),
]

# Patterns that signal 'something failed' so the recap can warn.
_FAILURE_PATTERNS = [
    re.compile(r"\b(error|failed|exception|crash|broken|regress)\b", re.IGNORECASE),
    re.compile(r"\b(test\s+failed|build\s+failed|CI\s+failed)\b", re.IGNORECASE),
]

# Phrases the user types when explicitly asking 'what's next' — the
# recap shouldn't try to answer those, it just summarises state.
_NEXT_HINTS = [
    re.compile(r"\b(now|next|then|after that|once that's done)\b", re.IGNORECASE),
]


@dataclass
class Recap:
    """Structured summary the REPL can render however it likes."""

    goal: str = ""
    status: str = ""
    next_step: str = ""
    turns_scanned: int = 0

    def is_empty(self) -> bool:
        return not (self.goal or self.status or self.next_step)

    def render(self) -> str:
        """One-paragraph Claude-Code-style line."""
        parts: list[str] = []
        if self.goal:
            parts.append(f"**Goal**: {self.goal}")
        if self.status:
            parts.append(f"**Status**: {self.status}")
        if self.next_step:
            parts.append(f"**Next**: {self.next_step}")
        return " · ".join(parts) if parts else "_(no clear goal in recent turns)_"


def build_recap(
    history: list[Message],
    max_turns: int = 30,
    session_id: str | None = None,
) -> Recap:
    """Walk the last N non-system turns and synthesize a Recap.

    Deterministic — same history in, same Recap out. Designed to run
    in ~1 ms over a 30-turn history. When `session_id` is passed,
    persisted goals (from session_memory) win over the heuristic
    history scan — that's the durable layer the agent records.
    """
    # Skip the system prompt at history[0]; keep only user / assistant /
    # tool messages, oldest-first within the tail.
    tail = [m for m in history if m.role in ("user", "assistant", "tool")][-max_turns:]
    recap = Recap(turns_scanned=len(tail))
    if not tail:
        return recap

    # ─── Goal: persisted goals win, fall back to history scan ─────
    if session_id:
        try:
            from openbro.core import session_memory

            goals = session_memory.open_goals(session_id, limit=1)
            if goals:
                recap.goal = _shorten(goals[0].text, 140)
        except Exception:
            pass

    if not recap.goal:
        for m in reversed(tail):
            if m.role != "user" or not m.content:
                continue
            line = (m.content.splitlines() or [""])[0].strip()
            for pat in _GOAL_PATTERNS:
                if pat.search(line):
                    recap.goal = _shorten(line, 140)
                    break
            if recap.goal:
                break

    # Fallback: if no clear goal-setting phrase, use the FIRST user turn
    # in this window — that's usually what kicked the conversation off.
    if not recap.goal:
        first_user = next((m for m in tail if m.role == "user" and m.content), None)
        if first_user:
            recap.goal = _shorten(first_user.content.splitlines()[0].strip(), 140)

    # ─── Status: scan recent assistant + tool turns ────────────────
    successes: list[str] = []
    failures: list[str] = []
    for m in tail[-8:]:  # focus on the last few turns
        text = (m.content or "")[:400]
        if m.role in ("assistant", "tool"):
            for pat in _SUCCESS_PATTERNS:
                if pat.search(text):
                    snippet = _first_sentence(text, max_chars=80)
                    if snippet:
                        successes.append(snippet)
                    break
            for pat in _FAILURE_PATTERNS:
                if pat.search(text):
                    snippet = _first_sentence(text, max_chars=80)
                    if snippet:
                        failures.append(snippet)
                    break

    if failures:
        recap.status = f"⚠ {failures[-1]}"
    elif successes:
        recap.status = f"✓ {successes[-1]}"
    else:
        recap.status = "in progress"

    # ─── Next step: heuristic from the last assistant turn ─────────
    # If the assistant ended with an explicit suggestion / question,
    # take that. Otherwise leave blank — the recap is honest about
    # not always knowing what's next.
    last_assistant = next((m for m in reversed(tail) if m.role == "assistant"), None)
    if last_assistant and last_assistant.content:
        recap.next_step = _extract_next_step(last_assistant.content)

    return recap


# ─── helpers ───────────────────────────────────────────────────────────


def _shorten(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _first_sentence(text: str, max_chars: int = 100) -> str:
    """Pull the first sentence-ish chunk out of `text`, capped at
    max_chars. Used for status/next summaries so we don't paste a
    400-char paragraph."""
    if not text:
        return ""
    # Stop at the first sentence boundary.
    m = re.search(r"(.+?[.!?])(\s|$)", text.strip())
    chunk = m.group(1) if m else text.split("\n", 1)[0]
    return _shorten(chunk, max_chars)


# Sentences that end with explicit next-step language — we prefer these
# when extracting 'Next' from the assistant's last response.
_NEXT_STEP_PATTERNS = [
    re.compile(
        r"(?:^|\n)\s*(?:next(?:\s+step)?|now|then|finally)[:\-]\s*(.{5,200})", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:you\s+can|you\s+should|try|run|test|verify|check)\b\s+(.{5,150})", re.IGNORECASE
    ),
]


def _extract_next_step(assistant_text: str) -> str:
    """Pull a 'Next' suggestion out of the assistant's last response."""
    if not assistant_text:
        return ""
    text = assistant_text.strip()
    # Tail-first scan so we get the suggestion at the END of the message.
    for pat in _NEXT_STEP_PATTERNS:
        matches = list(pat.finditer(text))
        if matches:
            cand = matches[-1].group(1).strip()
            cand = re.split(r"[.!?\n]", cand, maxsplit=1)[0]
            return _shorten(cand, 120)
    return ""
