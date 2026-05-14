"""LocalLLMProvider — replaces OllamaProvider.

Implements the OpenBro LLMProvider interface using llama-cpp-python directly.
No HTTP, no daemon — same in-process C++ engine that powers Ollama/LM Studio
underneath.

Wired into the router under provider id 'local' (and 'ollama' for backward
compatibility with existing user configs that say provider: ollama).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from openbro.llm.base import LLMProvider, LLMResponse, Message
from openbro.llm.local_engine import LocalEngine


def _extract_tool_calls_from_text(text: str, tools: list[dict]) -> list[dict]:
    """Lift inline-JSON tool calls out of plain text content.

    llama.cpp's structured tool-call support on Llama-3 / Mistral / Phi is
    unreliable: the model often outputs the tool call as a JSON object in
    the `content` field rather than populating `tool_calls`. We see things
    like:

        {"name": "file_ops", "parameters": {"action": "list", "path": "~/Desktop"}}
        {"name": "web_search", "arguments": {"query": "weather Delhi"}}

    Without this parser the user sees raw JSON in chat and nothing actually
    runs. We scan for `{...}` blocks whose `name` matches a registered tool
    and rebuild the OpenAI-style tool_calls list so agent._handle_tool_calls
    can execute them.
    """
    if not tools:
        return []
    tool_names = {t.get("name", "") for t in tools if t.get("name")}
    if not tool_names:
        return []

    text = (text or "").strip()
    if not text or "{" not in text:
        return []

    def _to_call(obj: dict) -> dict | None:
        name = obj.get("name")
        if name not in tool_names:
            return None
        args = obj.get("parameters") or obj.get("arguments") or {}
        # agent._handle_tool_calls reads function.arguments as a dict
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return {"function": {"name": name, "arguments": args or {}}}

    calls: list[dict] = []
    # First try parsing the whole thing as a single JSON object
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            call = _to_call(obj)
            if call:
                return [call]
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    call = _to_call(item)
                    if call:
                        calls.append(call)
            if calls:
                return calls
    except json.JSONDecodeError:
        pass

    # Walk the string finding balanced {...} blocks and try each
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : i + 1]
                start = -1
                try:
                    obj = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    call = _to_call(obj)
                    if call:
                        calls.append(call)
    return calls


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
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        # Llama.cpp often returns the tool call as a JSON string in content
        # rather than populating tool_calls. Recover it so the agent can
        # actually execute the tool instead of showing raw JSON to the user.
        if not tool_calls and tools and content.strip().startswith(("{", "[")):
            parsed = _extract_tool_calls_from_text(content, tools)
            if parsed:
                tool_calls = parsed
                content = ""  # the JSON WAS the tool call, not a reply

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
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
