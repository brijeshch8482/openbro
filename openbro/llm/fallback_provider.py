"""Auto-fallback provider — wraps a primary LLM with a backup.

The user's design intent: 'jisse user ko kabhi problem na ho'. When the
primary cloud provider hits a rate limit, network error, or transient
auth issue, we don't return an error message — we transparently re-issue
the same request to the fallback (typically a local llama.cpp model).
The user just sees a slightly slower response, not a failure.

Categorization of errors matters:
  - Recoverable (rate limit / network / 5xx) -> fall back, retry on
    primary later
  - Permanent (invalid auth, bad request, schema error) -> raise so the
    user actually sees what's wrong instead of getting silently
    degraded output

The wrapper preserves the LLMProvider interface so the agent loop and
existing tests don't need to know it exists. Bus events surface the
fallback to the UI (`status bar: 'cloud rate-limited, using local'`).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from openbro.llm.base import LLMProvider, LLMResponse, Message


class _FallbackChainExhausted(Exception):
    """Both primary and fallback failed — agent.py turns this into a
    calm user-facing message instead of leaking ValueError text."""

    def __init__(
        self,
        primary: str,
        primary_error: str,
        fallback: str,
        fallback_error: str,
    ):
        self.primary = primary
        self.primary_error = primary_error
        self.fallback = fallback
        self.fallback_error = fallback_error
        super().__init__(
            f"both providers failed — primary {primary}: "
            f"{primary_error[:200]}; fallback {fallback}: "
            f"{fallback_error[:200]}"
        )


# Error patterns that justify cascading to the fallback. Each substring
# is checked case-insensitively against str(exc) AND the type name.
# Kept conservative — when in doubt, raise, so the user knows.
_RECOVERABLE_PATTERNS = (
    # Rate limits / quota
    "429",
    "rate limit",
    "rate_limit",
    "tokens per minute",
    "tokens_per_minute",
    "request too large",
    "quota",
    "413",  # request entity too large
    # Server-side transients
    "500",
    "502",
    "503",
    "504",
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "overloaded",
    # Network
    "timeout",
    "timed out",
    "connection",
    "name resolution",
    "getaddrinfo",
    "remotedisconnected",
    "remote disconnected",
    "connection reset",
    "ssl",
    # Tool-call parser issues — primary models that mangle the schema
    # should fall back to a different one rather than failing the turn.
    "failed to call a function",
    "failed to parse tool call",
    "tool call validation failed",
)


def _is_recoverable(e: Exception) -> bool:
    """Decide whether the error is worth cascading on."""
    text = str(e).lower()
    type_name = type(e).__name__.lower()
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    if "connection" in type_name or "timeout" in type_name:
        return True
    return any(pat in text for pat in _RECOVERABLE_PATTERNS)


class FallbackProvider(LLMProvider):
    """Wrap two providers; cascade primary -> fallback on recoverable errors.

    The wrapper is constructed by the router when config sets both
    `llm.primary` (or `llm.provider`) AND `llm.fallback`. The agent and
    rest of the codebase see one provider through the LLMProvider
    interface — they don't know or care about the cascade.
    """

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider,
        on_fallback: Any = None,
    ):
        self.primary = primary
        self.fallback = fallback
        # Optional callback invoked when we actually cascade. The REPL
        # subscribes to log + status-bar update. Signature:
        #   on_fallback(primary_name: str, fallback_name: str, error: str)
        self.on_fallback = on_fallback
        # State the status bar reads to draw 'fallback active' indicator.
        self.last_used = "primary"  # "primary" | "fallback"
        self.fallback_count = 0  # cumulative cascades this session

    # ─── Interface methods ────────────────────────────────────────────

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        # Primary retry chain with backoff. Most cloud 5xx errors
        # clear within a few seconds; cascading to a slow local model
        # on the very first hiccup wastes a 30-90s GGUF load when a
        # 1-2s wait would have succeeded. Captured 2026-05-31: Groq
        # threw 503 frequently during a stress test; the local cascade
        # made every flap feel like a hard failure.
        e = self._try_primary_with_retry(messages, tools)
        if e is None:
            return self._last_primary_response  # type: ignore[return-value]
        self._notify(e)
        # Pre-trim before delegating: cloud primary often has 32K+
        # context but the local fallback is usually 8K. The tools
        # schema (~5-10K tokens for 23 tools) can exceed half the
        # local context BY ITSELF, so we shrink it first. The local
        # model then answers in plain text — less capable but it
        # produces a response instead of crashing.
        fb_tools = self._shrink_tools_for_fallback(tools)
        fb_messages = self._fit_to_fallback_context(messages, tools=fb_tools)
        # Mistral and some other local chat templates require strict
        # user/assistant alternation after the optional system prompt.
        # OpenBro's normal history can have multiple system messages
        # and tool/tool_result turns that break alternation. Captured
        # 2026-05-31: mistral-nemo raised 'After the optional system
        # message, conversation roles must alternate user/assist'.
        # Merge system blocks + collapse adjacent same-role turns.
        fb_messages = self._normalize_for_strict_alternation(fb_messages)
        try:
            response = self.fallback.chat(fb_messages, fb_tools)
            self.last_used = "fallback"
            self.fallback_count += 1
            return response
        except Exception as fb_err:
            # Both primary AND fallback failed. Surface a typed
            # exception so the agent's _friendly_error can render a
            # calm message instead of leaking a raw stack trace.
            raise _FallbackChainExhausted(
                primary=self.primary.name(),
                primary_error=str(e),
                fallback=self.fallback.name(),
                fallback_error=str(fb_err),
            ) from fb_err

    def stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> Iterator[str]:
        """Stream from the primary, fall back on first-chunk error.

        We can't cleanly switch providers mid-stream once tokens have
        been yielded to the consumer, so we try one chunk first. If the
        first chunk arrives, we trust the rest. If primary errors before
        any output, we cascade cleanly.
        """
        try:
            iterator = self.primary.stream(messages, tools)
            first = next(iterator)
        except StopIteration:
            # Primary produced empty stream. Treat as success.
            self.last_used = "primary"
            return
        except Exception as e:
            if not _is_recoverable(e):
                raise
            self._notify(e)
            self.last_used = "fallback"
            self.fallback_count += 1
            yield from self.fallback.stream(self._fit_to_fallback_context(messages), tools)
            return
        self.last_used = "primary"
        yield first
        # Even mid-stream errors after the first chunk we don't try to
        # recover — partial output is already with the user.
        try:
            yield from iterator
        except Exception:
            return

    def supports_tools(self) -> bool:
        # Conservative: must be true on BOTH so a tool-using call doesn't
        # silently fall back to a tool-less model and lose information.
        # In practice both Groq and llama.cpp providers report True.
        return self.primary.supports_tools() and self.fallback.supports_tools()

    def name(self) -> str:
        return f"{self.primary.name()}+{self.fallback.name()}"

    # ─── Helpers ─────────────────────────────────────────────────────

    def _shrink_tools_for_fallback(self, tools: list[dict] | None) -> list[dict] | None:
        """Decide whether to forward the tools schema to the fallback.

        Local llama.cpp models typically have 8K context vs cloud's
        32K+. OpenBro registers 23 tools whose JSON schema runs to
        20-40K characters (~5-10K tokens). When that exceeds half
        the fallback's context the request crashes BEFORE any
        history is even considered.

        Strategy: if the serialised tools cost > 30% of the fallback
        context, drop tools entirely. The local model answers as
        plain text — less capable than tool-using mode but it CAN
        produce a response. Better degraded answer than a raw
        ValueError shown to the user.
        """
        if not tools:
            return tools
        ctx = self._fallback_context_limit()
        if ctx is None:
            return tools
        try:
            import json as _json

            tools_tokens = len(_json.dumps(tools)) // 4
        except Exception:
            return tools
        if tools_tokens > ctx * 0.3:
            return None  # drop schema; degraded text-only mode
        return tools

    # Backoff delays (seconds) before each primary attempt. First
    # attempt is immediate (0.0), then 1s, then 3s. Tests patch
    # `time.sleep` to no-op so they don't wait 4s per cascade.
    _PRIMARY_RETRY_DELAYS = (0.0, 1.0, 3.0)

    def _try_primary_with_retry(
        self,
        messages: list[Message],
        tools: list[dict] | None,
    ) -> Exception | None:
        """Try the primary up to len(_PRIMARY_RETRY_DELAYS) times.

        On success: stashes the response in `self._last_primary_response`
        and returns None.
        On final recoverable failure: returns the last exception so
        the caller can decide whether to cascade to the fallback.
        On non-recoverable failure: raises immediately (auth, schema
        errors aren't worth retrying or falling back on).
        """
        last_err: Exception | None = None
        for delay in self._PRIMARY_RETRY_DELAYS:
            if delay > 0:
                time.sleep(delay)
            try:
                response = self.primary.chat(messages, tools)
                self.last_used = "primary"
                self._last_primary_response = response
                return None
            except Exception as e:
                if not _is_recoverable(e):
                    raise
                last_err = e
        return last_err

    def _normalize_for_strict_alternation(self, messages: list[Message]) -> list[Message]:
        """Reshape history so it satisfies strict user/assistant
        alternation after at most one system message at the start.

        Required by Mistral-family chat templates (mistral-nemo,
        mixtral) and some other local models. OpenBro's normal
        history can contain:
          • Multiple `system` messages (initial + reflection retries +
            transient context blocks)
          • `tool` role messages (results piped back to the model)
          • Sequences of same-role messages

        Normalisation:
          1. Concatenate every `system` message into ONE at index 0.
          2. Convert `tool` role into `user` (tool results read as
             follow-up user input from the model's perspective).
          3. Collapse adjacent same-role messages into one.
          4. Ensure the first non-system message is `user` (drop a
             leading orphan assistant).
        """
        if not messages:
            return messages

        sys_chunks: list[str] = []
        rest: list[Message] = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    sys_chunks.append(m.content)
            else:
                rest.append(m)

        # tool → user
        normalised: list[Message] = []
        for m in rest:
            role = "user" if m.role == "tool" else m.role
            normalised.append(Message(role=role, content=m.content, tool_calls=m.tool_calls))

        # Drop leading assistant (no user before it would violate
        # alternation — Mistral expects user-first after system).
        while normalised and normalised[0].role == "assistant":
            normalised.pop(0)

        # Collapse adjacent same-role messages.
        collapsed: list[Message] = []
        for m in normalised:
            if collapsed and collapsed[-1].role == m.role:
                prev = collapsed[-1]
                merged_content = (prev.content or "") + "\n\n" + (m.content or "")
                collapsed[-1] = Message(
                    role=prev.role,
                    content=merged_content,
                    tool_calls=prev.tool_calls or m.tool_calls,
                )
            else:
                collapsed.append(m)

        out: list[Message] = []
        if sys_chunks:
            out.append(Message(role="system", content="\n\n".join(sys_chunks)))
        out.extend(collapsed)
        return out

    def _fallback_context_limit(self) -> int | None:
        """Return the fallback provider's max context in tokens, or
        None if we can't determine it.

        Reads from `fallback.engine.n_ctx` (LocalLLMProvider) or
        `fallback.n_ctx` (custom configs). Returning None means 'no
        trim' — safer than trimming aggressively against an unknown
        budget.
        """
        eng = getattr(self.fallback, "engine", None)
        if eng is not None and getattr(eng, "n_ctx", None):
            return int(eng.n_ctx)
        n = getattr(self.fallback, "n_ctx", None)
        if isinstance(n, int) and n > 0:
            return n
        return None

    def _fit_to_fallback_context(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> list[Message]:
        """Trim `messages` so the fallback's context window can hold them.

        - Drops [TRANSIENT_RESEARCH] / [TRANSIENT_PLAN] system blocks
          (these are usually 5K+ chars each — pruning them alone
          often brings the request under budget).
        - Keeps the initial system prompt at index 0.
        - Tail-keeps the most recent messages until the budget is hit.
        - Token estimate: 4 chars ≈ 1 token. Reserves room for both
          the response AND the tools-schema input (~5-7K tokens for
          23 tools — Llama.cpp counts the tools JSON as input).

        Returns the original list when no trim is needed or when the
        fallback's context can't be determined.
        """
        ctx = self._fallback_context_limit()
        if ctx is None or not messages:
            return messages
        # Reserve: 1500 for response + estimated tools schema cost.
        # Tools schema serialised as JSON costs ~250 chars per tool
        # (name + description + 3-5 params). 23 tools ≈ 5750 chars
        # ≈ 1450 tokens. Budget the worst case.
        tools_tokens = 0
        if tools:
            try:
                import json as _json

                tools_tokens = len(_json.dumps(tools)) // 4
            except Exception:
                tools_tokens = 1500
        budget = max(1024, ctx - 1500 - tools_tokens)

        # Step 1: drop transient context blocks.
        trimmed = [
            m
            for m in messages
            if not (
                m.role == "system"
                and (
                    "[TRANSIENT_RESEARCH]" in (m.content or "")
                    or "[TRANSIENT_PLAN]" in (m.content or "")
                )
            )
        ]

        def _approx(msg: Message) -> int:
            return max(1, len(msg.content or "") // 4)

        used = sum(_approx(m) for m in trimmed)
        if used <= budget:
            return trimmed

        # Step 2: keep system[0] + walk tail-first within budget.
        kept = [trimmed[0]] if trimmed and trimmed[0].role == "system" else []
        used = _approx(trimmed[0]) if kept else 0
        tail: list = []
        for msg in reversed(trimmed[1:] if kept else trimmed):
            cost = _approx(msg)
            if used + cost > budget:
                break
            tail.append(msg)
            used += cost
        tail.reverse()
        return kept + tail

    def _notify(self, e: Exception) -> None:
        """Fire the registered callback (UI uses it for status updates)."""
        if self.on_fallback is None:
            return
        try:
            self.on_fallback(self.primary.name(), self.fallback.name(), str(e))
        except Exception:
            # Never let a UI callback crash the agent.
            pass
