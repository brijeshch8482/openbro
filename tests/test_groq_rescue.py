"""Tests for the Groq inline tool-call rescue parsers.

Llama-family models on Groq routinely emit tool calls inside chat content
instead of the structured tool_calls slot. Two formats seen in the wild:

  - JSON: `[{"name": "X", "parameters": {...}}]` or `{"name": ..., "arguments": ...}`
  - XML-style: `<function=NAME>{"key": "val"}</function>` (bare or closed)

Both are rescued by `_extract_inline_tool_calls` so the agent can still
dispatch instead of showing raw text to the user.
"""

from openbro.llm.groq_provider import (
    _extract_function_tag_calls,
    _extract_inline_tool_calls,
)


def test_function_tag_bare_form():
    """Llama-4 captured: `<function=network>{"action": "ip"}` (no closing tag)."""
    captured = (
        "Ek minute, bro! Mai IP address find karta hoon.\n"
        '<function=network>{"action": "ip"}'
    )
    calls = _extract_inline_tool_calls(captured)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "network"
    assert calls[0]["function"]["arguments"] == {"action": "ip"}


def test_function_tag_closed_form():
    closed = (
        'Sure: <function=file_ops>{"action":"open","path":"a.pdf"}</function>'
    )
    calls = _extract_inline_tool_calls(closed)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "file_ops"
    assert calls[0]["function"]["arguments"] == {
        "action": "open",
        "path": "a.pdf",
    }


def test_function_tag_multiple():
    multi = (
        '<function=tool_a>{"x":1}</function>\n'
        '<function=tool_b>{"y":2}</function>'
    )
    calls = _extract_inline_tool_calls(multi)
    assert len(calls) == 2
    assert calls[0]["function"]["name"] == "tool_a"
    assert calls[1]["function"]["name"] == "tool_b"


def test_function_tag_empty_args_object():
    calls = _extract_function_tag_calls("<function=ping>{}</function>")
    assert len(calls) == 1
    assert calls[0]["function"]["arguments"] == {}


def test_json_array_form_still_works():
    """Pre-existing rescue: array of {name, parameters} objects."""
    json_form = '[{"name": "word", "parameters": {"action": "read"}}]'
    calls = _extract_inline_tool_calls(json_form)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "word"
    assert calls[0]["function"]["arguments"] == {"action": "read"}


def test_json_object_form_with_arguments_key():
    json_form = '{"name": "file_ops", "arguments": {"action": "list"}}'
    calls = _extract_inline_tool_calls(json_form)
    assert len(calls) == 1
    assert calls[0]["function"]["arguments"] == {"action": "list"}


def test_code_fenced_json_form():
    fenced = '```json\n[{"name": "ping", "args": {"host": "a.com"}}]\n```'
    calls = _extract_inline_tool_calls(fenced)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "ping"
    assert calls[0]["function"]["arguments"] == {"host": "a.com"}


def test_plain_prose_returns_empty():
    assert _extract_inline_tool_calls("Hello, here is your answer.") == []


def test_empty_input_returns_empty():
    assert _extract_inline_tool_calls("") == []
    assert _extract_inline_tool_calls("   ") == []
