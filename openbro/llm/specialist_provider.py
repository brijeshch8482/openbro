"""LLMProvider implementation that routes to category specialists.

Inserts between the existing provider switch (groq / anthropic / etc.)
and the chat loop. On every chat() call:

  1. Route the latest user message through openbro.specialists.router.
  2. Ask the adapter engine to attach the matching LoRA (or fall back
     to the base model).
  3. Generate locally and return the result in the LLMProvider shape
     that openbro.core.agent already understands.

Tool calling, system prompts, and multi-turn context get folded into a
single chat string here — the specialists are 360 M parameters, so we
deliberately keep this layer simple rather than supporting OpenAI-style
tool_calls. Heavy reasoning is what cloud fallbacks are for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openbro.llm.base import LLMProvider, LLMResponse
from openbro.specialists.adapter_engine import AdapterEngine
from openbro.specialists.router import Router


@dataclass
class SpecialistInfo:
    """Snapshot of which specialist served the latest response."""

    slug: str
    matched_keyword: str | None
    method: str
    elapsed_ms: float


class SpecialistProvider(LLMProvider):
    """LLMProvider that fronts the local specialist tree."""

    def __init__(
        self,
        base_model: str = "HuggingFaceTB/SmolLM2-360M-Instruct",
        adapters_dir: str = "D:/OpenBro-teting/specialists/adapters",
    ) -> None:
        self._engine = AdapterEngine(base_model=base_model, adapters_dir=adapters_dir)
        self._router = Router()
        self.last_route: SpecialistInfo | None = None

    # ─── LLMProvider interface ────────────────────────────────────

    def name(self) -> str:
        return "specialist/openbro-tree"

    def supports_tools(self) -> bool:
        # Local specialists call tools by emitting <tool_call> blocks
        # in plain text. Agent loop parses them — no native function
        # calling API.
        return False

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> LLMResponse:
        prompt = _flatten_messages(messages)
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"),
            None,
        )
        routing_text = (last_user or {}).get("content", prompt)
        route = self._router.route(routing_text)
        result = self._engine.chat(
            prompt,
            slug=route.slug,
            max_new_tokens=max_tokens,
            temperature=temperature,
        )
        self.last_route = SpecialistInfo(
            slug=route.slug,
            matched_keyword=route.matched_keyword,
            method=route.method,
            elapsed_ms=route.elapsed_ms,
        )
        # Token usage isn't measured here (local inference) — pass
        # zeros so the status line doesn't break.
        return LLMResponse(
            content=result.text,
            tool_calls=[],
            usage={"input": 0, "output": 0},
        )


# ─── Helpers ─────────────────────────────────────────────────────────


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Turn the chat history into a single string the specialist can
    read. The adapter engine re-applies the chat template internally,
    so here we just concatenate role-tagged lines."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not content:
            continue
        if role == "system":
            parts.append(f"[system] {content}")
        elif role == "user":
            parts.append(content)
        elif role == "assistant":
            parts.append(f"(previous answer: {content})")
    return "\n".join(parts)
