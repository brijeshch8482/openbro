"""OpenAI-style chat messages, shared by every provider that speaks them.

The model must see its own tool calls and their results as STRUCTURED
history: an assistant turn with `tool_calls` (each with an id), then one
`role="tool"` message per call carrying that `tool_call_id`. If the calls are
dropped, the model never learns it already acted — it calls the same tool
again and again — and strict APIs (OpenAI, DeepSeek) reject a tool result
that answers no call.
"""

from __future__ import annotations

import json

from openbro.llm.base import Message


def _arguments_json(arguments) -> str:
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def to_openai_messages(messages: list[Message]) -> list[dict]:
    """Messages in OpenAI chat format, tool calls and results paired by id.

    Calls stored without an id (older transcripts, providers that return
    none) get a stable one here, and the results that follow them are
    matched to them in order, so every result answers a call."""
    out: list[dict] = []
    pending: list[str] = []  # ids of the last assistant turn's calls, in order
    for index, m in enumerate(messages):
        item: dict = {"role": m.role, "content": m.content or ""}
        if m.role == "assistant" and m.tool_calls:
            pending = []
            calls = []
            for n, tc in enumerate(m.tool_calls):
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                call_id = tc.get("id") or f"call_{index}_{n}"
                pending.append(call_id)
                calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": fn.get("name", ""),
                            "arguments": _arguments_json(fn.get("arguments")),
                        },
                    }
                )
            item["tool_calls"] = calls
        elif m.role == "tool":
            call_id = m.tool_call_id
            if call_id in pending:
                pending.remove(call_id)
            elif pending:
                call_id = pending.pop(0)
            if call_id:
                item["tool_call_id"] = call_id
            else:
                # A result that answers no call: strict APIs reject that, so
                # pass it on as plain context instead.
                item = {"role": "user", "content": f"[tool result]\n{m.content or ''}"}
        out.append(item)
    return out


def parse_arguments(arguments) -> dict:
    """Tool-call arguments as a dict (APIs return them as a JSON string)."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            value = json.loads(arguments)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}
