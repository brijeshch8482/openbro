"""Context compaction (harness piece 2, memory L2).

Keeps the history the agent sends under a token budget WITHOUT silently
forgetting: old tool output is cleared first, then older turns are
summarised into one rolling summary, and only as a last resort is the
current turn shrunk. Cuts only ever happen on turn boundaries (a turn
starts at a `user` message), so an assistant `tool_calls` message is never
separated from its `tool` results.

History shape handled here:

    [system prompt] [summary system message (optional)] turn, turn, …
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from openbro.llm.base import Message

SUMMARY_MARKER = "[CONVERSATION_SUMMARY]"
CHARS_PER_TOKEN = 4


@dataclass
class CompactionConfig:
    max_context_tokens: int = 24000
    trigger_ratio: float = 0.75
    keep_recent_turns: int = 6
    clear_tool_output_after_turns: int = 3
    tool_output_keep_chars: int = 1000
    summary_max_tokens: int = 800
    # Optional count trigger (agent.max_history). None = tokens only.
    max_messages: int | None = None

    @classmethod
    def from_config(cls, config: dict) -> CompactionConfig:
        mem = (config or {}).get("memory", {}) or {}
        comp = mem.get("compaction", {}) or {}
        agent = (config or {}).get("agent", {}) or {}
        cfg = cls()
        cfg.max_context_tokens = int(mem.get("max_context_tokens", cfg.max_context_tokens))
        for name in (
            "trigger_ratio",
            "keep_recent_turns",
            "clear_tool_output_after_turns",
            "tool_output_keep_chars",
            "summary_max_tokens",
        ):
            if name in comp:
                setattr(cfg, name, type(getattr(cfg, name))(comp[name]))
        max_history = agent.get("max_history")
        cfg.max_messages = int(max_history) if max_history else None
        return cfg


@dataclass
class CompactionResult:
    history: list[Message]
    tokens_before: int
    tokens_after: int
    cleared_tool_outputs: int = 0
    summarised_turns: int = 0
    dropped_steps: int = 0
    summary: str | None = None
    summary_upto: str | None = None
    stages: list[str] = field(default_factory=list)

    def describe(self) -> str:
        parts = [f"{self.tokens_before:,} → {self.tokens_after:,} tokens"]
        if self.summarised_turns:
            parts.append(f"summarised {self.summarised_turns} turns")
        if self.cleared_tool_outputs:
            parts.append(f"cleared {self.cleared_tool_outputs} old tool outputs")
        if self.dropped_steps:
            parts.append(f"dropped {self.dropped_steps} steps of the current turn")
        detail = f" ({', '.join(parts[1:])})" if parts[1:] else ""
        return f"context compacted: {parts[0]}{detail}"


# ─── measuring ───────────────────────────────────────────────────────


def message_tokens(msg: Message) -> int:
    size = len(msg.content or "")
    if msg.tool_calls:
        size += len(json.dumps(msg.tool_calls, ensure_ascii=False))
    return size // CHARS_PER_TOKEN + 4  # + per-message overhead


def estimate_tokens(messages: list[Message]) -> int:
    return sum(message_tokens(m) for m in messages)


# ─── structure helpers ───────────────────────────────────────────────


def is_summary(msg: Message) -> bool:
    return msg.role == "system" and (msg.content or "").startswith(SUMMARY_MARKER)


def split_head(history: list[Message]) -> tuple[list[Message], Message | None, list[Message]]:
    """-> (system prompt part, summary message or None, conversation body)."""
    head: list[Message] = []
    i = 0
    if history and history[0].role == "system" and not is_summary(history[0]):
        head = [history[0]]
        i = 1
    summary = None
    if i < len(history) and is_summary(history[i]):
        summary = history[i]
        i += 1
    return head, summary, list(history[i:])


def split_turns(body: list[Message]) -> list[list[Message]]:
    """Group messages into turns; a turn starts at each `user` message.
    A leading group with no user message (left over from an earlier cut)
    is returned as its own first group."""
    turns: list[list[Message]] = []
    for msg in body:
        if msg.role == "user" or not turns:
            turns.append([msg])
        else:
            turns[-1].append(msg)
    return turns


SUMMARY_HEADER = (
    "Summary of the earlier part of this conversation (older turns were compacted to save context):"
)


def summary_text(msg: Message | None) -> str | None:
    """The summary body without the marker and header line."""
    if msg is None:
        return None
    text = (msg.content or "")[len(SUMMARY_MARKER) :].strip()
    if text.startswith(SUMMARY_HEADER):
        text = text[len(SUMMARY_HEADER) :].strip()
    return text or None


def make_summary_message(text: str) -> Message:
    return Message(
        role="system",
        content=f"{SUMMARY_MARKER}\n{SUMMARY_HEADER}\n\n{text.strip()}",
    )


def repair_tool_pairs(messages: list[Message]) -> list[Message]:
    """Make a restored history API-valid.

    Drops an assistant `tool_calls` message whose results are not all
    present (e.g. the process died mid-turn) and any `tool` result that no
    preceding assistant message asked for. An assistant message with text
    and missing results keeps its text but loses the dangling calls.
    """
    out: list[Message] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if msg.role == "tool":
            i += 1  # orphan — its assistant message was not kept
            continue
        if msg.role == "assistant" and msg.tool_calls:
            wanted = [c.get("id", "") for c in msg.tool_calls]
            j = i + 1
            results: list[Message] = []
            while j < n and messages[j].role == "tool":
                results.append(messages[j])
                j += 1
            got = {r.tool_call_id for r in results}
            if all(w in got for w in wanted):
                out.append(msg)
                out.extend(r for r in results if r.tool_call_id in wanted)
            elif msg.content:
                out.append(Message(role="assistant", content=msg.content, id=msg.id))
            i = j
            continue
        out.append(msg)
        i += 1
    return out


def _units(turn: list[Message]) -> list[list[Message]]:
    """Split one turn (after its user message) into indivisible units:
    an assistant tool_calls message + its tool results, or a single message."""
    units: list[list[Message]] = []
    for msg in turn[1:]:
        if msg.role == "tool" and units and units[-1][0].tool_calls:
            units[-1].append(msg)
        else:
            units.append([msg])
    return units


def _fallback_summary(prior: str | None, turns: list[list[Message]]) -> str:
    lines = []
    if prior:
        lines.append(prior.strip())
        lines.append("")
    lines.append("Earlier requests (automatic summary unavailable):")
    for t in turns:
        first = t[0]
        if first.role == "user":
            text = " ".join((first.content or "").split())
            lines.append(f"- {text[:200]}")
    return "\n".join(lines)


# ─── the compactor ───────────────────────────────────────────────────

# summarize(prior_summary, messages_to_summarise, focus) -> summary text
Summarizer = Callable[[str | None, list[Message], str | None], str]


class Compactor:
    def __init__(self, config: CompactionConfig, summarize: Summarizer | None = None):
        self.config = config
        self.summarize = summarize
        # Tokens sent with every call but not part of the history (native
        # tool schemas). Reserved out of the budget.
        self.reserved_tokens = 0

    @property
    def budget(self) -> int:
        return self.config.max_context_tokens

    def _limit(self) -> int:
        limit = int(self.config.max_context_tokens * self.config.trigger_ratio)
        return max(limit - self.reserved_tokens, 0)

    def tokens_over(self, history: list[Message]) -> bool:
        return estimate_tokens(history) > self._limit()

    def is_over(self, history: list[Message]) -> bool:
        if self.tokens_over(history):
            return True
        if self.config.max_messages:
            _, _, body = split_head(history)
            if len(body) > self.config.max_messages:
                return True
        return False

    def fit(
        self,
        history: list[Message],
        force: bool = False,
        focus: str | None = None,
    ) -> CompactionResult | None:
        """Return a compacted history, or None when nothing needed doing.

        `force=True` (the `compact` command) summarises every complete
        turn regardless of size.
        """
        if not force and not self.is_over(history):
            return None
        before = estimate_tokens(history)
        head, summary_msg, body = split_head(history)
        turns = split_turns(repair_tool_pairs(body))
        result = CompactionResult(history=history, tokens_before=before, tokens_after=before)

        def assemble(summary: Message | None, ts: list[list[Message]]) -> list[Message]:
            return head + ([summary] if summary else []) + [m for t in ts for m in t]

        # Stage 1 — clear bulky tool output in older turns.
        cfg = self.config
        cutoff = max(len(turns) - cfg.clear_tool_output_after_turns, 0)
        for t in turns[:cutoff]:
            for k, msg in enumerate(t):
                if msg.role == "tool" and len(msg.content or "") > cfg.tool_output_keep_chars:
                    t[k] = Message(
                        role="tool",
                        content=(
                            f"[tool output cleared — {len(msg.content):,} chars"
                            f"{_tool_name_suffix(t, msg)}]"
                        ),
                        tool_call_id=msg.tool_call_id,
                        id=msg.id,
                    )
                    result.cleared_tool_outputs += 1
        if result.cleared_tool_outputs:
            result.stages.append("clear_tool_output")
        current = assemble(summary_msg, turns)
        if not force and not self.is_over(current):
            return self._finish(result, current)

        # Stage 2 — summarise older turns.
        placeholder = make_summary_message("x" * (cfg.summary_max_tokens * CHARS_PER_TOKEN))
        if force:
            keep = 0
        else:
            keep = max(1, min(cfg.keep_recent_turns, len(turns)))
            while keep > 1 and self.is_over(assemble(placeholder, turns[-keep:])):
                keep -= 1
        old = turns[: len(turns) - keep] if keep else list(turns)
        recent = turns[len(turns) - keep :] if keep else []
        if old:
            prior = summary_text(summary_msg)
            flat_old = [m for t in old for m in t]
            text = None
            if self.summarize is not None:
                try:
                    text = (self.summarize(prior, flat_old, focus) or "").strip() or None
                except Exception:
                    text = None
            if text is None:
                text = _fallback_summary(prior, old)
            summary_msg = make_summary_message(text)
            result.summary = text
            result.summary_upto = next((m.id for m in reversed(flat_old) if m.id), None)
            result.summarised_turns = sum(1 for t in old if t and t[0].role == "user")
            result.stages.append("summarise")
            turns = recent
        current = assemble(summary_msg, turns)
        # Stage 3 runs on tokens only: a long-but-small current turn is
        # real work in progress, not something a message count should cut.
        if force or not self.tokens_over(current) or len(turns) != 1:
            return self._finish(result, current)

        # Stage 3 — the current turn alone is over: drop its oldest steps.
        turn = turns[0]
        user_msg, units = turn[0], _units(turn)
        dropped = 0
        while len(units) > 1 and self.tokens_over(assemble(summary_msg, [[user_msg]] + units)):
            dropped += len(units.pop(0))
        if dropped:
            user_msg = Message(
                role=user_msg.role,
                content=(
                    f"{user_msg.content}\n\n[note: {dropped} earlier messages of this task "
                    "were dropped to fit the context window]"
                ),
                id=user_msg.id,
            )
            result.dropped_steps = dropped
            result.stages.append("shrink_current_turn")
        current = assemble(summary_msg, [[user_msg] + [m for u in units for m in u]])
        return self._finish(result, current)

    @staticmethod
    def _finish(result: CompactionResult, history: list[Message]) -> CompactionResult | None:
        if not result.stages:
            return None
        result.history = history
        result.tokens_after = estimate_tokens(history)
        return result


def _tool_name_suffix(turn: list[Message], tool_msg: Message) -> str:
    for msg in turn:
        for call in msg.tool_calls or []:
            if call.get("id") == tool_msg.tool_call_id:
                name = (call.get("function") or {}).get("name")
                if name:
                    return f" from {name}"
    return ""


# ─── summariser prompt (used by the agent) ───────────────────────────

SUMMARY_SYSTEM_PROMPT = (
    "You compact a conversation between a user and OpenBro, an AI agent that "
    "uses tools on the user's computer. Write a summary that lets the agent "
    "continue the work without the original messages.\n\n"
    "Use exactly these sections, short bullet points, no preamble:\n"
    "## Goal\n## Done\n## Decisions\n## Open items\n## Key facts & paths\n\n"
    "Keep exact file paths, names, numbers, commands and error messages. "
    "Write in the language the user writes in. Do not call tools."
)


def render_for_summary(messages: list[Message], per_tool_chars: int = 1500) -> str:
    """Plain transcript for the summariser, tool output truncated."""
    lines: list[str] = []
    for m in messages:
        if m.role == "user":
            lines.append(f"USER: {m.content}")
        elif m.role == "assistant":
            if m.content:
                lines.append(f"ASSISTANT: {m.content}")
            for call in m.tool_calls or []:
                fn = call.get("function") or {}
                args = fn.get("arguments", {})
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                lines.append(f"TOOL CALL {fn.get('name', '?')}: {args[:500]}")
        elif m.role == "tool":
            text = m.content or ""
            if len(text) > per_tool_chars:
                text = text[:per_tool_chars] + f" …[{len(m.content) - per_tool_chars:,} more chars]"
            lines.append(f"TOOL RESULT: {text}")
    return "\n".join(lines)
