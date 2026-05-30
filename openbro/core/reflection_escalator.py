"""ReflectionEscalator — multi-round retry chain with strategy switching.

Captured failures across May 2026:
  - 'iss time mera phone laptop se connected hai ya nhi?' — 1 retry
    cap was hit, second attempt still fabricated, user saw a lie.
  - 'in think there is some faill in github push?' — multi-step
    follow-up where single retry wasn't enough to recover.
  - 'kitne try krta hai claude' — user explicit ask: 'claude jaise
    unlimited try karo'.

The naive 'unbounded retry of the same call' is wrong — a weak model
on the same prompt will produce the same fabrication forever. The
fix: **unbounded BUT escalating** — every retry SWITCHES strategy,
not just retries.

Default chain (6 rounds total, ~30-60 sec worst case):

    Round 1  primary call           (no strategy applied yet)
    Round 2  harder_prompt          stricter reflection injection
    Round 3  model_swap_maverick    switch family within Groq
    Round 4  fallback_local         local llama.cpp (slow but reliable)
    Round 5  simplify_query         strip transient context, focus
    Round 6  honest_stop            tell user we couldn't, with summary

Each round emits an `escalation_round` bus event for the REPL renderer.
User can interrupt anytime via Ctrl+C. The escalator is per-turn —
each `Agent._chat_impl` call gets a fresh instance.

Why not infinite rounds?
  - llama.cpp local takes 5-15 sec per call (round 4 alone)
  - History grows linearly: every reflection adds a system message,
    every retry adds an assistant message + tool result, so round N
    is more expensive than round N-1.
  - Capped at 6 because every captured failure mode has been
    recovered by round 4 max in dogfooding.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Strategy:
    """A single recovery strategy applied on one escalation round.

    Attributes:
        name: short kebab-case id for events + logging.
        description: 5-10 word Hinglish description for REPL UI.
        prompt_injection: system message to append before retrying.
            Empty string means no injection (used for `honest_stop`).
        model_swap: provider model id to switch to for this round.
            None = keep current model. Special sentinel 'LOCAL' means
            switch to the configured local fallback provider.
        simplify: when True, the escalator hints to the agent to drop
            transient context (research / plan markers) before retry.
        is_honest_stop: marks the terminal 'we couldn't' message.
    """

    name: str
    description: str
    prompt_injection: str = ""
    model_swap: str | None = None
    simplify: bool = False
    is_honest_stop: bool = False


# The default chain. Order matters — each round escalates the
# previous. Tested against the captured failures: NDLS_FDB
# fabrication, network-args render, MTP kiosk lazy, research-cascade.
DEFAULT_STRATEGIES: tuple[Strategy, ...] = (
    Strategy(
        name="harder_prompt",
        description="stricter reflection prompt",
        prompt_injection=(
            "[REFLECTION RETRY 2/6] Your previous answer was unusable "
            "(fabricated output, lazy filler, or rendered-args-as-text). "
            "Retry NOW with these hard rules:\n"
            "  1. If a tool is needed → emit a real tool_call. Never "
            "type the call as chat text.\n"
            "  2. If you can answer from prior tool results → write a "
            "specific 2-4 line answer with concrete details from the "
            "results. No 'I cannot directly test', no 'consider X', no "
            "'you should consult Y'.\n"
            "  3. If you genuinely lack data → say 'I need to call "
            "<tool>' and emit the call. Don't promise without "
            "executing.\n"
            "  4. Never invent Output: blocks. Never echo tool args as "
            "code. The user has the real output already."
        ),
    ),
    Strategy(
        name="model_swap_maverick",
        description="switch to llama-4-maverick (different family)",
        prompt_injection=(
            "[REFLECTION RETRY 3/6] Model has been swapped. Apply the "
            "same hard rules: tool calls must be real, answers must be "
            "specific, no fabrication."
        ),
        model_swap="meta-llama/llama-4-maverick-17b-128e-instruct",
    ),
    Strategy(
        name="fallback_local",
        description="fall back to local model (slower, reliable)",
        prompt_injection=(
            "[REFLECTION RETRY 4/6] Switched to local model. Be "
            "concise and direct — if you need a tool, call it; "
            "otherwise give a 2-4 line specific answer. No "
            "boilerplate."
        ),
        model_swap="LOCAL",
    ),
    Strategy(
        name="simplify_query",
        description="strip transient context, focus on core question",
        prompt_injection=(
            "[REFLECTION RETRY 5/6] Transient research / plan context "
            "has been pruned. Re-read the original user message and "
            "answer it directly — one tool call if needed, then a "
            "specific 2-4 line response. Nothing else."
        ),
        simplify=True,
    ),
    Strategy(
        name="honest_stop",
        description="stop honestly — model can't complete this",
        is_honest_stop=True,
    ),
)


@dataclass
class ReflectionEscalator:
    """Tracks which strategies have been used this turn.

    Usage from `Agent._chat_impl`:

        escalator = ReflectionEscalator()
        ...
        if fabricated_reason or lazy_markers:
            strategy = escalator.next_strategy(trigger=fabricated_reason)
            if strategy is None or strategy.is_honest_stop:
                # emit honest stop, return
            else:
                # apply strategy: inject prompt, swap model, etc.
                continue

    The escalator does NOT mutate the agent — caller applies the
    Strategy. This keeps the class testable in isolation.
    """

    strategies: Sequence[Strategy] = field(default_factory=lambda: DEFAULT_STRATEGIES)
    _index: int = 0
    _history: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)

    def next_strategy(self, trigger: str = "") -> Strategy | None:
        """Return the next strategy to apply, or None if exhausted.

        `trigger` is the reason this escalation fired (fabrication
        marker, lazy phrase, etc.) — recorded for the honest-stop
        summary at the end.
        """
        if self._index >= len(self.strategies):
            return None
        strategy = self.strategies[self._index]
        self._index += 1
        self._history.append(strategy.name)
        if trigger:
            self.triggers.append(trigger)
        return strategy

    def exhausted(self) -> bool:
        return self._index >= len(self.strategies)

    def rounds_used(self) -> int:
        return self._index

    @property
    def history(self) -> list[str]:
        return list(self._history)

    def summary(self) -> str:
        """Render the list of strategies tried — used in the
        honest-stop message so the user sees what was attempted."""
        if not self._history:
            return "no rounds attempted"
        return ", ".join(self._history)

    def build_honest_stop_message(self, last_trigger: str = "") -> str:
        """Compose the user-facing 'we tried, here's what failed'
        message. Called when the chain reaches honest_stop or
        next_strategy returns None.
        """
        rounds = self.rounds_used()
        tried = self.summary()
        trigger = last_trigger or (self.triggers[-1] if self.triggers else "model couldn't recover")
        return (
            f"I tried {rounds} different strategies to answer this "
            f"({tried}) but my model couldn't produce a real tool "
            f"call or a grounded answer. Last issue: {trigger}.\n\n"
            "Options:\n"
            "  • Rephrase the request more concretely (e.g. `run X`, "
            "`check file Y`)\n"
            "  • Try `/fallback local` to pin local for this session\n"
            "  • Ask `/recap` to see the goal state\n"
        )
