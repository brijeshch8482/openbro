"""Groq LLM provider - ultra-fast inference, free tier available."""

import json

import httpx

from openbro.llm.base import LLMProvider, LLMResponse, Message

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self.api_key = api_key
        self.model = model

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

        resp = httpx.post(GROQ_API_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            # raise_for_status drops the response body — Groq puts the real
            # reason ('model not found', 'messages[0].content too short',
            # tool-call validation failure) there. Surface it so the user
            # can actually see what went wrong.
            body = ""
            try:
                err = resp.json()
                body = err.get("error", {}).get("message") or json.dumps(err)
            except (ValueError, KeyError):
                body = resp.text[:500]
            raise RuntimeError(f"Groq {resp.status_code}: {body}")
        data = resp.json()

        choice = data["choices"][0]["message"]
        tool_calls = []
        if choice.get("tool_calls"):
            for tc in choice["tool_calls"]:
                tool_calls.append(
                    {
                        "id": tc["id"],
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": json.loads(tc["function"]["arguments"]),
                        },
                    }
                )

        return LLMResponse(
            content=choice.get("content", "") or "",
            tool_calls=tool_calls,
            usage={
                "input": data.get("usage", {}).get("prompt_tokens", 0),
                "output": data.get("usage", {}).get("completion_tokens", 0),
            },
            model=self.model,
        )

    def supports_tools(self) -> bool:
        return True

    def name(self) -> str:
        return f"groq/{self.model}"
