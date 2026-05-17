"""Groq LLM provider - ultra-fast inference, free tier available."""

import json
import re

import httpx

from openbro.llm.base import LLMProvider, LLMResponse, Message

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


# Llama 3.3 70B on Groq has a known function-call serialization quirk:
# instead of emitting {"name": "web", "arguments": "{\"action\":\"search\"}"},
# it sometimes emits {"name": "web={\"action\":\"search\"}", "arguments": "{}"}
# — the args get glued INTO the name field. Groq's tool-call validator then
# rejects the next request because 'web={...}' isn't in the tools list,
# returning a 400. We salvage the call by detecting `<name>={<json>}` and
# splitting it back out before the agent loop sees the response.
_GLUED_NAME = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\{.*\})$", re.DOTALL)


def _sanitize_tool_call(name: str, arguments: str | dict) -> tuple[str, dict]:
    """Recover (name, args dict) from Llama-on-Groq glued tool calls."""
    if isinstance(arguments, dict):
        args = arguments
    else:
        try:
            args = json.loads(arguments) if arguments else {}
        except (TypeError, ValueError):
            args = {}

    if isinstance(name, str):
        m = _GLUED_NAME.match(name.strip())
        if m:
            real_name, glued_args = m.group(1), m.group(2)
            try:
                parsed = json.loads(glued_args)
                if isinstance(parsed, dict):
                    # Glued args win — they're what the model meant to send.
                    args = parsed
                    name = real_name
            except (TypeError, ValueError):
                # Glob looked like name={...} but the {...} isn't valid JSON.
                # Keep the original name; the agent will report 'unknown tool'
                # and the LLM gets a chance to retry.
                pass
    return name, args


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
                raw_name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"]
                clean_name, clean_args = _sanitize_tool_call(raw_name, raw_args)
                tool_calls.append(
                    {
                        "id": tc["id"],
                        "function": {
                            "name": clean_name,
                            "arguments": clean_args,
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
