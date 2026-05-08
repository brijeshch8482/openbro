"""DeepSeek provider — OpenAI-compatible API at api.deepseek.com.

Cheap (~$0.14/M tokens) and surprisingly strong at reasoning + tool use.
Uses the openai client library since the wire format matches.
"""

from __future__ import annotations

from collections.abc import Iterator

from openbro.llm.base import LLMProvider, LLMResponse, Message

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import openai

            self._client = openai.OpenAI(api_key=self.api_key, base_url=DEEPSEEK_BASE_URL)
            return self._client
        except ImportError as e:
            raise RuntimeError(
                "openai client not installed. Run: pip install 'openbro[openai]'"
            ) from e

    def name(self) -> str:
        return f"deepseek/{self.model}"

    def supports_tools(self) -> bool:
        return True

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        client = self._get_client()
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools and self.supports_tools():
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        try:
            resp = client.chat.completions.create(**payload)
            msg = resp.choices[0].message
            tool_calls = []
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    tool_calls.append(
                        {
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        }
                    )
            return LLMResponse(content=msg.content or "", tool_calls=tool_calls)
        except Exception as e:
            return LLMResponse(content=f"DeepSeek error: {e}", tool_calls=[])

    def stream(self, messages: list[Message], tools: list[dict] | None = None) -> Iterator[str]:
        client = self._get_client()
        try:
            stream = client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"\nDeepSeek stream error: {e}"
