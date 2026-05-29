"""Anthropic (Claude) LLM provider — first-class implementation.

The previous version was a stub that broke as soon as the agent loop ran
multi-turn: it serialized tool results as `role='tool'` (Claude rejects
that), dropped tool_use blocks from assistant turns, and shipped OpenAI-
style tool schemas Claude doesn't understand.

This rewrite handles every shape the Anthropic Messages API actually
expects:

  - System messages: passed as the top-level `system=` kwarg, NOT in
    `messages`. With prompt caching enabled, the system block gets a
    `cache_control` marker so subsequent requests within 5 minutes pay
    ~10% the input cost.

  - Tools schema: translated from OpenAI-style
    `{"function": {"name": ..., "parameters": ...}}` to Claude-style
    `{"name": ..., "input_schema": ...}`. Falls through unchanged if a
    schema is already in Claude format.

  - Assistant turns that called tools: serialized as a content array
    containing both `text` blocks AND `tool_use` blocks (with the
    original id, name, input). The agent's `Message.tool_calls` carries
    the structured info from a prior LLMResponse; we faithfully render
    it back so the model sees its own past tool calls.

  - Tool result turns: ANY message with `role='tool'` is rewritten to
    `role='user'` with a single `tool_result` content block referencing
    `tool_use_id` and carrying the result string. This is the ONLY shape
    Claude's tool-use loop will accept.

  - Streaming: implemented via `messages.stream(...)`. Yields each text
    chunk as it arrives so the REPL's live render flows naturally.

  - Error handling: maps the most common Anthropic exceptions to the
    same Hinglish-friendly shapes `_friendly_error` in agent.py expects
    (rate limit / auth / network / overloaded).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from openbro.llm.base import LLMProvider, LLMResponse, Message

# Model aliases so users can type short forms in config or the REPL
# without remembering the dated suffix. Resolved at construction time.
MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-20250514",
    "sonnet-4": "claude-sonnet-4-20250514",
    "claude-sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
    "opus-4": "claude-opus-4-20250514",
    "claude-opus": "claude-opus-4-20250514",
    "haiku": "claude-haiku-4-5-20251001",
    "haiku-4": "claude-haiku-4-5-20251001",
    "claude-haiku": "claude-haiku-4-5-20251001",
}


def _resolve_model(model: str) -> str:
    key = (model or "").strip().lower()
    return MODEL_ALIASES.get(key, model)


def _translate_tools(tools: list[dict] | None) -> list[dict] | None:
    """Convert OpenAI-style tool schemas to Claude `input_schema` shape.

    OpenAI / Groq pass:
        {"name": ..., "description": ..., "parameters": {...}}
    or sometimes:
        {"type": "function", "function": {"name": ..., "parameters": ...}}

    Claude wants flat:
        {"name": ..., "description": ..., "input_schema": {...}}

    Anything already in Claude shape passes through unchanged.
    """
    if not tools:
        return None
    out: list[dict] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        # Already-Claude shape
        if "input_schema" in t and "name" in t:
            out.append(t)
            continue
        # OpenAI nested shape: {"type": "function", "function": {...}}
        inner = t.get("function") if "function" in t else t
        name = inner.get("name") or t.get("name")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": inner.get("description", t.get("description", "")),
                "input_schema": inner.get(
                    "parameters",
                    inner.get("input_schema", {"type": "object", "properties": {}}),
                ),
            }
        )
    return out or None


def _to_anthropic_messages(messages: list[Message]) -> tuple[str | None, list[dict]]:
    """Split system + chat, convert each turn to Claude's expected shape.

    Returns (system_text, chat_messages). System is passed separately to
    `messages.create(system=...)` — NOT inside the messages list.

    Tool round-trips:
      - Assistant turn with `tool_calls` → content array with `text` +
        one `tool_use` block per call.
      - Tool result turn (role='tool', has tool_call_id) → rewritten to
        role='user' with a single `tool_result` block.
      - Consecutive role='tool' results are merged into one user turn
        because Claude requires alternating user/assistant turns.
    """
    system_parts: list[str] = []
    out: list[dict] = []
    pending_tool_results: list[dict] = []

    def _flush_tool_results() -> None:
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for m in messages:
        if m.role == "system":
            if m.content:
                system_parts.append(m.content)
            continue

        if m.role == "tool":
            # Tool result → user/tool_result block. Buffer to merge runs.
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id or "",
                    "content": m.content or "",
                }
            )
            continue

        # Non-tool message — flush any pending tool results first so the
        # message order stays right.
        _flush_tool_results()

        if m.role == "assistant":
            # Assistant turn — could be text-only, tool_use-only, or both.
            blocks: list[dict] = []
            text = (m.content or "").strip()
            if text:
                blocks.append({"type": "text", "text": text})
            for call in m.tool_calls or []:
                fn = call.get("function") or {}
                name = fn.get("name") or call.get("name") or ""
                args = fn.get("arguments")
                if args is None:
                    args = call.get("input") or call.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        import json as _json

                        args = _json.loads(args)
                    except (TypeError, ValueError):
                        args = {}
                if not isinstance(args, dict):
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "",
                        "name": name,
                        "input": args,
                    }
                )
            if not blocks:
                # Empty assistant turn — skip (Claude rejects empty content).
                continue
            # If the assistant turn is pure text, send the simpler string
            # form. Mixing tool_use forces the content-array form.
            if len(blocks) == 1 and blocks[0]["type"] == "text":
                out.append({"role": "assistant", "content": blocks[0]["text"]})
            else:
                out.append({"role": "assistant", "content": blocks})
            continue

        # role='user' — plain user message.
        if m.role == "user":
            out.append({"role": "user", "content": m.content or ""})
            continue

        # Unknown role — drop it rather than crash the request.

    _flush_tool_results()
    return ("\n\n".join(system_parts) or None, out)


def _wrap_system_with_cache(system: str | None, enable_cache: bool) -> Any:
    """Wrap the system prompt for prompt caching when enabled.

    Anthropic's prompt-caching feature lets us tag the system block with
    `cache_control: {type: 'ephemeral'}`. Subsequent requests within
    5 minutes that share the same prefix pay ~10% of the input cost on
    that prefix. Massive win for agent loops that re-send the same
    system prompt every turn.
    """
    if not system:
        return None
    if not enable_cache:
        return system
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API provider with proper tool-use round-trips."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 8192,
        enable_prompt_caching: bool = True,
    ):
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "Install anthropic: pip install 'openbro[anthropic]' "
                "(or pip install 'anthropic>=0.40')"
            ) from e
        self._anthropic_mod = anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = _resolve_model(model)
        self.max_tokens = int(max_tokens)
        self.enable_prompt_caching = bool(enable_prompt_caching)

    def supports_tools(self) -> bool:
        return True

    def name(self) -> str:
        return f"anthropic/{self.model}"

    # ─── chat (non-streaming) ─────────────────────────────────────────

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        system, chat_msgs = _to_anthropic_messages(messages)
        wrapped_system = _wrap_system_with_cache(system, self.enable_prompt_caching)
        translated_tools = _translate_tools(tools)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": chat_msgs,
        }
        if wrapped_system is not None:
            kwargs["system"] = wrapped_system
        if translated_tools:
            # When prompt caching is on, cache the last tool block too —
            # tools schema is just as static as the system prompt.
            if self.enable_prompt_caching and translated_tools:
                cached = [dict(t) for t in translated_tools]
                cached[-1] = dict(cached[-1])
                cached[-1]["cache_control"] = {"type": "ephemeral"}
                kwargs["tools"] = cached
            else:
                kwargs["tools"] = translated_tools

        try:
            resp = self.client.messages.create(**kwargs)
        except Exception as e:
            # Re-raise with the original type intact so agent's _friendly_error
            # can branch on it. We DON'T swallow the exception here.
            raise self._wrap_error(e) from e

        return self._build_response(resp)

    # ─── stream ───────────────────────────────────────────────────────

    def stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> Iterator[str]:
        """Stream text chunks. Tool calls are NOT streamed — the agent loop
        uses non-streaming chat() for those. This method is for the REPL's
        plain-text fast path on providers that don't have structured tools.
        """
        system, chat_msgs = _to_anthropic_messages(messages)
        wrapped_system = _wrap_system_with_cache(system, self.enable_prompt_caching)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": chat_msgs,
        }
        if wrapped_system is not None:
            kwargs["system"] = wrapped_system
        if tools:
            kwargs["tools"] = _translate_tools(tools)

        try:
            with self.client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield text
        except Exception as e:
            raise self._wrap_error(e) from e

    # ─── helpers ──────────────────────────────────────────────────────

    def _build_response(self, resp: Any) -> LLMResponse:
        content = ""
        tool_calls: list[dict] = []
        for block in resp.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                content += block.text
            elif btype == "tool_use":
                # input on the SDK is already a python dict
                tool_calls.append(
                    {
                        "id": block.id,
                        "function": {"name": block.name, "arguments": block.input or {}},
                    }
                )
        usage = getattr(resp, "usage", None)
        # Sonnet 4 also reports cache_read_input_tokens / cache_creation_input_tokens;
        # surface them so the UI can show prompt-cache savings.
        usage_dict: dict[str, int] = {}
        if usage is not None:
            usage_dict["input"] = getattr(usage, "input_tokens", 0) or 0
            usage_dict["output"] = getattr(usage, "output_tokens", 0) or 0
            cr = getattr(usage, "cache_read_input_tokens", 0) or 0
            cc = getattr(usage, "cache_creation_input_tokens", 0) or 0
            if cr:
                usage_dict["cache_read"] = cr
            if cc:
                usage_dict["cache_creation"] = cc

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage_dict,
            model=self.model,
        )

    def _wrap_error(self, e: Exception) -> Exception:
        """Tag the exception type so agent._friendly_error matches cleanly.

        We don't actually wrap — just re-emit. Kept as a method so future
        provider-specific translation has a clean hook.
        """
        msg = str(e)
        try:
            api_err = self._anthropic_mod.APIError  # type: ignore[attr-defined]
            if isinstance(e, api_err) and not msg:
                # Some Anthropic exceptions stringify to empty — surface the
                # type name so the friendly-error matcher has something.
                return type(e)(f"{type(e).__name__}")
        except Exception:
            pass
        return e
