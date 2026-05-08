"""Google Gemini provider — uses Google AI Studio API.

Free tier available; great for large-context tasks. API spec is
similar to OpenAI but with its own endpoint and request shape.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from openbro.llm.base import LLMProvider, LLMResponse, Message

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GoogleProvider(LLMProvider):
    """Provider for Google's Gemini models (gemini-1.5-pro, gemini-2.0-flash, etc.)."""

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model = model

    def name(self) -> str:
        return f"google/{self.model}"

    def supports_tools(self) -> bool:
        return True

    @staticmethod
    def _to_gemini_messages(messages: list[Message]) -> tuple[str, list[dict]]:
        """Translate OpenBro messages to Gemini's contents+systemInstruction shape."""
        system = ""
        contents = []
        for m in messages:
            if m.role == "system":
                system = (system + "\n" + m.content).strip()
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m.content}]})
        return system, contents

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        system, contents = self._to_gemini_messages(messages)
        payload: dict = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools and self.supports_tools():
            # Gemini wants a slightly different tool spec — map our schemas
            payload["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t.get("parameters", {}),
                        }
                        for t in tools
                    ]
                }
            ]

        url = f"{API_BASE}/models/{self.model}:generateContent?key={self.api_key}"
        try:
            r = httpx.post(url, json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPStatusError as e:
            return LLMResponse(content=f"Gemini API error {e.response.status_code}", tool_calls=[])
        except Exception as e:
            return LLMResponse(content=f"Gemini error: {e}", tool_calls=[])

        # Extract text + tool calls from Gemini's response
        text_parts = []
        tool_calls = []
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                if "text" in part:
                    text_parts.append(part["text"])
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(
                        {
                            "function": {
                                "name": fc.get("name", ""),
                                "arguments": fc.get("args", {}),
                            }
                        }
                    )
        return LLMResponse(content="\n".join(text_parts).strip(), tool_calls=tool_calls)

    def stream(self, messages: list[Message], tools: list[dict] | None = None) -> Iterator[str]:
        # Gemini supports streaming via :streamGenerateContent — use SSE
        system, contents = self._to_gemini_messages(messages)
        payload: dict = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        url = f"{API_BASE}/models/{self.model}:streamGenerateContent?alt=sse&key={self.api_key}"
        try:
            with httpx.stream("POST", url, json=payload, timeout=60) as r:
                for line in r.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[len("data: ") :].strip()
                    if data == "[DONE]" or not data:
                        continue
                    try:
                        import json

                        ev = json.loads(data)
                        for cand in ev.get("candidates", []):
                            for part in cand.get("content", {}).get("parts", []):
                                if "text" in part:
                                    yield part["text"]
                    except Exception:
                        continue
        except Exception as e:
            yield f"\nGemini stream error: {e}"
