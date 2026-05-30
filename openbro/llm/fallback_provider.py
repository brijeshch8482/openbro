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

from collections.abc import Iterator
from typing import Any

from openbro.llm.base import LLMProvider, LLMResponse, Message

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
        try:
            response = self.primary.chat(messages, tools)
            self.last_used = "primary"
            return response
        except Exception as e:
            if not _is_recoverable(e):
                # Non-recoverable (auth, schema, model not found, etc.)
                # Surface it so the user can fix the config.
                raise
            self._notify(e)
            # Pre-trim before delegating: cloud primary often has
            # 32K+ context but the local fallback is usually 8K.
            # Captured 2026-05-30: 'this is...app so you have to
            # see' triggered Groq 503 → fallback to local llama3.2:3b
            # → ValueError 'requested (10876) exceed context window
            # of 8192' was the user's only response.
            fb_messages = self._fit_to_fallback_context(messages)
            response = self.fallback.chat(fb_messages, tools)
            self.last_used = "fallback"
            self.fallback_count += 1
            return response

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

    def _fit_to_fallback_context(self, messages: list[Message]) -> list[Message]:
        """Trim `messages` so the fallback's context window can hold them.

        - Drops [TRANSIENT_RESEARCH] / [TRANSIENT_PLAN] system blocks
          (these are usually 5K+ chars each — pruning them alone
          often brings the request under budget).
        - Keeps the initial system prompt at index 0.
        - Tail-keeps the most recent messages until the budget is hit.
        - Token estimate: 4 chars ≈ 1 token. Reserves ~1.5K for the
          response so the model has room to reply.

        Returns the original list when no trim is needed or when the
        fallback's context can't be determined.
        """
        ctx = self._fallback_context_limit()
        if ctx is None or not messages:
            return messages
        budget = max(1024, ctx - 1500)

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
