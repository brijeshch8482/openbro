"""LocalLLMProvider — replaces OllamaProvider.

Implements the OpenBro LLMProvider interface using llama-cpp-python directly.
No HTTP, no daemon — same in-process C++ engine that powers Ollama/LM Studio
underneath.

Wired into the router under provider id 'local' (and 'ollama' for backward
compatibility with existing user configs that say provider: ollama).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from openbro.llm.base import LLMProvider, LLMResponse, Message
from openbro.llm.local_engine import LocalEngine


class LocalLLMProvider(LLMProvider):
    """LLMProvider backed by llama-cpp-python."""

    def __init__(
        self,
        model_path: str | Path,
        model_name: str | None = None,
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        chat_format: str | None = None,
    ):
        self.engine = LocalEngine(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            chat_format=chat_format,
        )
        self.model_path = Path(model_path)
        self.model_name = model_name or self.model_path.stem

    def _to_dicts(self, messages: list[Message]) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        response = self.engine.chat(self._to_dicts(messages), tools=tools)
        choices = response.get("choices") or []
        if not choices:
            return LLMResponse(content="", model=self.model_name)
        msg = choices[0].get("message") or {}
        return LLMResponse(
            content=msg.get("content") or "",
            tool_calls=msg.get("tool_calls") or [],
            usage=response.get("usage") or {},
            model=self.model_name,
        )

    def stream(self, messages: list[Message], tools: list[dict] | None = None) -> Iterator[str]:
        # llama.cpp doesn't stream tool calls usefully today — fall back to
        # non-streaming chat() when tools are required.
        if tools:
            yield self.chat(messages, tools=tools).content
            return
        yield from self.engine.stream(self._to_dicts(messages))

    def supports_tools(self) -> bool:
        return True

    def name(self) -> str:
        return f"local/{self.model_name}"

    def is_available(self) -> bool:
        return self.model_path.exists()
