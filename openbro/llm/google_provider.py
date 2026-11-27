"""Google Gemini provider — uses Google AI Studio API.

Free tier available; great for large-context tasks. API spec is
similar to OpenAI but with its own endpoint and request shape.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx

from openbro.llm.base import LLMProvider, LLMResponse, Message
from openbro.llm.wire import parse_arguments

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GoogleProvider(LLMProvider):
    """Provider for Google's Gemini models (gemini-1.5-pro, gemini-2.0-flash, etc.)."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model

    def name(self) -> str:
        return f"google/{self.model}"

    def supports_tools(self) -> bool:
        return True

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key}

    @staticmethod
    def _to_gemini_messages(messages: list[Message]) -> tuple[str, list[dict]]:
        """Translate OpenBro messages to Gemini's contents+systemInstruction shape.

        Tool calls go back as `functionCall` parts on the model turn and their
        results as `functionResponse` parts, all results of one round in one
        user turn. Without them Gemini never sees that it already called a
        tool, so it calls it again and again."""
        system = ""
        contents: list[dict] = []
        names_by_id: dict[str, str] = {}
        pending_names: list[str] = []  # calls of the last model turn, in order
        for m in messages:
            if m.role == "system":
                system = (system + "\n" + (m.content or "")).strip()
            elif m.role == "assistant":
                parts: list[dict] = [{"text": m.content}] if m.content else []
                pending_names = []
                for call in m.tool_calls or []:
                    fn = call.get("function", {}) if isinstance(call, dict) else {}
                    name = fn.get("name", "")
                    if not name:
                        continue
                    args = parse_arguments(fn.get("arguments"))
                    part: dict = {"functionCall": {"name": name, "args": args}}
                    if call.get("thought_signature"):
                        part["thoughtSignature"] = call["thought_signature"]
                    parts.append(part)
                    names_by_id[call.get("id") or ""] = name
                    pending_names.append(name)
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif m.role == "tool":
                name = names_by_id.get(m.tool_call_id or "") or (
                    pending_names.pop(0) if pending_names else ""
                )
                if not name:  # a result we can't pair: show it as text
                    contents.append(
                        {"role": "user", "parts": [{"text": f"[tool result]\n{m.content}"}]}
                    )
                    continue
                part = {"functionResponse": {"name": name, "response": {"result": m.content}}}
                last = contents[-1] if contents else None
                if last and last.get("_results"):
                    last["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part], "_results": True})
            else:
                contents.append({"role": "user", "parts": [{"text": m.content or " "}]})
        for c in contents:
            c.pop("_results", None)
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

        # The key goes in a header, never the URL: URLs end up in error
        # messages and logs.
        url = f"{API_BASE}/models/{self.model}:generateContent"
        # Let HTTP and connection errors RAISE so the FallbackProvider
        # can categorise them (429/5xx as recoverable → retry +
        # cascade to local; 401/403/400 as non-recoverable → surface
        # so the user fixes config). Captured 2026-05-31: previously
        # we caught and returned a fake LLMResponse with
        # 'Gemini API error 429' as content, which the FallbackProvider
        # treated as a successful turn — so neither the 1+2 retry chain
        # nor the local cascade fired. User saw the literal error text
        # as the final answer.
        r = httpx.post(url, json=payload, headers=self._headers(), timeout=60)
        r.raise_for_status()
        data = r.json()

        # Extract text + tool calls from Gemini's response
        text_parts = []
        tool_calls = []
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                if "text" in part:
                    text_parts.append(part["text"])
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    call = {
                        # Gemini gives no call id; one is needed to pair the
                        # result with this call when the history goes back.
                        "id": f"gemini_{uuid.uuid4().hex[:12]}",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": fc.get("args", {}),
                        },
                    }
                    # Thinking models sign their calls; the signature must be
                    # sent back with the call or the next request may fail.
                    if part.get("thoughtSignature"):
                        call["thought_signature"] = part["thoughtSignature"]
                    tool_calls.append(call)
        # Gemini reports tokens via usageMetadata. Captured 2026-05-31:
        # status bar showed '0↓ 0↑ tokens' for every Gemini turn
        # because we weren't passing the usage dict into LLMResponse.
        # Now mapped to the same {input, output} shape the rest of
        # the agent (token counter, fallback context fit) expects.
        usage_meta = data.get("usageMetadata", {}) or {}
        usage = {
            "input": int(usage_meta.get("promptTokenCount", 0) or 0),
            "output": int(usage_meta.get("candidatesTokenCount", 0) or 0),
        }
        return LLMResponse(
            content="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            usage=usage,
        )

    def stream(self, messages: list[Message], tools: list[dict] | None = None) -> Iterator[str]:
        # Gemini supports streaming via :streamGenerateContent — use SSE
        system, contents = self._to_gemini_messages(messages)
        payload: dict = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        url = f"{API_BASE}/models/{self.model}:streamGenerateContent?alt=sse"
        try:
            with httpx.stream("POST", url, json=payload, headers=self._headers(), timeout=60) as r:
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
