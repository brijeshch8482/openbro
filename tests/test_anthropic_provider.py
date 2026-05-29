"""Anthropic (Claude) provider tests.

Cover every conversion that previously broke multi-turn loops:

  - Tool schema OpenAI -> Claude (nested {"function": ...} and flat)
  - System message extraction (separated from messages list)
  - Tool result rewriting (role='tool' -> role='user' with tool_result block)
  - Assistant turn with tool_calls -> content blocks (text + tool_use)
  - Consecutive tool results merged into one user turn
  - Multi-turn round-trip (system + user + assistant+tool_use + tool result + ...)
  - Model alias resolution ('sonnet' -> full ID)
  - Prompt-caching wrapper on system + tools
  - Streaming yields text chunks
  - Usage extraction including cache_read / cache_creation tokens
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openbro.llm.anthropic_provider import (
    MODEL_ALIASES,
    AnthropicProvider,
    _resolve_model,
    _to_anthropic_messages,
    _translate_tools,
    _wrap_system_with_cache,
)
from openbro.llm.base import Message

# ─── Helpers ───────────────────────────────────────────────────────────


def _make_provider(monkeypatch, model="claude-sonnet-4-20250514", caching=True):
    """Construct an AnthropicProvider with the SDK call site stubbed."""
    fake_anthropic = MagicMock()
    fake_client = MagicMock()
    fake_anthropic.Anthropic.return_value = fake_client

    # Patch the import inside the provider module
    with patch.dict("sys.modules", {"anthropic": fake_anthropic}):
        provider = AnthropicProvider(
            api_key="test-key",
            model=model,
            enable_prompt_caching=caching,
        )
    provider.client = fake_client  # ensure tests use the same fake
    return provider, fake_client


# ─── Model alias resolution ────────────────────────────────────────────


def test_model_alias_sonnet():
    assert _resolve_model("sonnet") == "claude-sonnet-4-20250514"
    assert _resolve_model("Sonnet") == "claude-sonnet-4-20250514"


def test_model_alias_opus_haiku():
    assert _resolve_model("opus") == "claude-opus-4-20250514"
    assert _resolve_model("haiku") == "claude-haiku-4-5-20251001"


def test_model_alias_passthrough_unknown():
    """An unknown string passes through untouched — user might be on
    a future model the alias table doesn't know yet."""
    assert _resolve_model("claude-future-2099") == "claude-future-2099"


def test_model_aliases_table_covers_known_families():
    assert "sonnet" in MODEL_ALIASES
    assert "opus" in MODEL_ALIASES
    assert "haiku" in MODEL_ALIASES


# ─── Tool schema translation ───────────────────────────────────────────


def test_translate_tools_openai_nested():
    """OpenAI / Groq nested shape: {type:'function', function:{name,...}}"""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "file_ops",
                "description": "File ops",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    out = _translate_tools(tools)
    assert out is not None
    assert len(out) == 1
    assert out[0]["name"] == "file_ops"
    assert out[0]["description"] == "File ops"
    assert "input_schema" in out[0]
    assert "parameters" not in out[0]


def test_translate_tools_openai_flat():
    """Some tools registered with {name, description, parameters} at top level."""
    tools = [
        {
            "name": "shell",
            "description": "Run shell",
            "parameters": {"type": "object"},
        }
    ]
    out = _translate_tools(tools)
    assert out is not None
    assert out[0]["name"] == "shell"
    assert out[0]["input_schema"] == {"type": "object"}


def test_translate_tools_already_claude_shape_passthrough():
    """If the schema is already in Claude shape, leave it alone."""
    tools = [
        {
            "name": "ping",
            "description": "ping",
            "input_schema": {"type": "object", "properties": {"host": {"type": "string"}}},
        }
    ]
    out = _translate_tools(tools)
    assert out == tools


def test_translate_tools_none_or_empty():
    assert _translate_tools(None) is None
    assert _translate_tools([]) is None


def test_translate_tools_skips_invalid_entries():
    """Entries without a name are skipped (defensive)."""
    tools = [
        {"description": "no name"},
        "not a dict",
        {"function": {"description": "still no name"}},
        {"name": "ok", "description": "ok"},
    ]
    out = _translate_tools(tools)
    assert out is not None
    assert len(out) == 1
    assert out[0]["name"] == "ok"


# ─── Message conversion ────────────────────────────────────────────────


def test_system_message_extracted_to_kwarg():
    msgs = [
        Message(role="system", content="You are OpenBro."),
        Message(role="user", content="Hello"),
    ]
    system, chat = _to_anthropic_messages(msgs)
    assert system == "You are OpenBro."
    assert chat == [{"role": "user", "content": "Hello"}]


def test_multiple_system_messages_concatenated():
    msgs = [
        Message(role="system", content="Rule 1"),
        Message(role="system", content="Rule 2"),
        Message(role="user", content="hi"),
    ]
    system, _ = _to_anthropic_messages(msgs)
    assert "Rule 1" in system
    assert "Rule 2" in system


def test_assistant_text_only_uses_string_content():
    msgs = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="Hello!"),
    ]
    _, chat = _to_anthropic_messages(msgs)
    assert chat[1]["content"] == "Hello!"  # plain string, not array


def test_assistant_with_tool_calls_uses_content_blocks():
    """The critical bug fix: assistant tool_calls must round-trip as
    content blocks, not be silently dropped."""
    msgs = [
        Message(role="user", content="open chrome"),
        Message(
            role="assistant",
            content="Let me open chrome.",
            tool_calls=[
                {
                    "id": "toolu_abc",
                    "function": {
                        "name": "app",
                        "arguments": {"action": "open", "app_name": "chrome"},
                    },
                }
            ],
        ),
    ]
    _, chat = _to_anthropic_messages(msgs)
    assistant_turn = chat[1]
    assert assistant_turn["role"] == "assistant"
    blocks = assistant_turn["content"]
    assert isinstance(blocks, list)
    assert any(b.get("type") == "text" for b in blocks)
    tool_use = next(b for b in blocks if b.get("type") == "tool_use")
    assert tool_use["id"] == "toolu_abc"
    assert tool_use["name"] == "app"
    assert tool_use["input"] == {"action": "open", "app_name": "chrome"}


def test_tool_result_rewritten_to_user_role():
    """The other critical bug: role='tool' messages MUST become role='user'
    with a tool_result content block — Claude rejects role='tool'."""
    msgs = [
        Message(role="user", content="open chrome"),
        Message(
            role="assistant",
            content="",
            tool_calls=[{"id": "toolu_abc", "function": {"name": "app", "arguments": {}}}],
        ),
        Message(role="tool", content="Opened: chrome.exe", tool_call_id="toolu_abc"),
    ]
    _, chat = _to_anthropic_messages(msgs)
    tool_turn = chat[2]
    assert tool_turn["role"] == "user"  # rewritten from 'tool'
    blocks = tool_turn["content"]
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "toolu_abc"
    assert blocks[0]["content"] == "Opened: chrome.exe"


def test_consecutive_tool_results_merged_into_one_user_turn():
    """If two tool_use blocks both return results, both must land in the
    SAME user turn (Claude requires alternating user/assistant)."""
    msgs = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {"id": "t1", "function": {"name": "a", "arguments": {}}},
                {"id": "t2", "function": {"name": "b", "arguments": {}}},
            ],
        ),
        Message(role="tool", content="result-1", tool_call_id="t1"),
        Message(role="tool", content="result-2", tool_call_id="t2"),
        Message(role="assistant", content="done"),
    ]
    _, chat = _to_anthropic_messages(msgs)
    # Should be: assistant(tool_use x2), user(tool_result x2), assistant("done")
    assert chat[0]["role"] == "assistant"
    assert chat[1]["role"] == "user"
    blocks = chat[1]["content"]
    assert len(blocks) == 2
    assert all(b["type"] == "tool_result" for b in blocks)
    assert chat[2]["role"] == "assistant"


def test_stringified_tool_arguments_get_parsed():
    """Some upstream paths serialize tool args as a JSON string. The
    conversion should re-parse so Claude sees a real object."""
    import json

    msgs = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "x",
                    "function": {
                        "name": "f",
                        "arguments": json.dumps({"a": 1}),
                    },
                }
            ],
        ),
    ]
    _, chat = _to_anthropic_messages(msgs)
    tool_use = next(b for b in chat[0]["content"] if b["type"] == "tool_use")
    assert tool_use["input"] == {"a": 1}


def test_empty_assistant_message_skipped():
    """An assistant message with no text and no tool calls would crash
    Claude — we drop it instead."""
    msgs = [
        Message(role="user", content="hi"),
        Message(role="assistant", content=""),
        Message(role="user", content="hello"),
    ]
    _, chat = _to_anthropic_messages(msgs)
    assert len(chat) == 2
    assert all(m["role"] == "user" for m in chat)


# ─── Prompt caching ────────────────────────────────────────────────────


def test_wrap_system_with_cache_enabled():
    out = _wrap_system_with_cache("You are X.", enable_cache=True)
    assert isinstance(out, list)
    assert out[0]["type"] == "text"
    assert out[0]["text"] == "You are X."
    assert out[0]["cache_control"] == {"type": "ephemeral"}


def test_wrap_system_with_cache_disabled():
    out = _wrap_system_with_cache("You are X.", enable_cache=False)
    assert out == "You are X."


def test_wrap_system_with_cache_none():
    assert _wrap_system_with_cache(None, enable_cache=True) is None


# ─── Provider construction ────────────────────────────────────────────


def test_provider_resolves_model_alias_at_init(monkeypatch):
    provider, _ = _make_provider(monkeypatch, model="sonnet")
    assert provider.model == "claude-sonnet-4-20250514"


def test_provider_name_includes_model(monkeypatch):
    provider, _ = _make_provider(monkeypatch, model="opus")
    assert "claude-opus-4-20250514" in provider.name()


def test_provider_supports_tools(monkeypatch):
    provider, _ = _make_provider(monkeypatch)
    assert provider.supports_tools() is True


# ─── chat() end-to-end ────────────────────────────────────────────────


def _fake_anthropic_response(
    text: str = "Hello",
    tool_uses: list[tuple[str, str, dict]] | None = None,
    usage: dict | None = None,
):
    """Mock anthropic response.content + .usage shape."""
    content_blocks = []
    if text:
        content_blocks.append(SimpleNamespace(type="text", text=text))
    for tid, name, inp in tool_uses or []:
        content_blocks.append(SimpleNamespace(type="tool_use", id=tid, name=name, input=inp))
    u = usage or {"input": 100, "output": 20}
    return SimpleNamespace(
        content=content_blocks,
        usage=SimpleNamespace(
            input_tokens=u.get("input", 0),
            output_tokens=u.get("output", 0),
            cache_read_input_tokens=u.get("cache_read", 0),
            cache_creation_input_tokens=u.get("cache_creation", 0),
        ),
    )


def test_chat_sends_translated_tools(monkeypatch):
    provider, fake_client = _make_provider(monkeypatch)
    fake_client.messages.create.return_value = _fake_anthropic_response("ok")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "ping",
                "description": "ping",
                "parameters": {"type": "object"},
            },
        }
    ]
    provider.chat([Message(role="user", content="hi")], tools=tools)

    kwargs = fake_client.messages.create.call_args.kwargs
    assert "tools" in kwargs
    tools_sent = kwargs["tools"]
    assert tools_sent[0]["name"] == "ping"
    # Caching enabled → last tool block gets cache_control
    assert tools_sent[-1].get("cache_control") == {"type": "ephemeral"}


def test_chat_sends_system_as_kwarg_not_in_messages(monkeypatch):
    provider, fake_client = _make_provider(monkeypatch)
    fake_client.messages.create.return_value = _fake_anthropic_response("hi")

    provider.chat(
        [
            Message(role="system", content="You are X"),
            Message(role="user", content="hello"),
        ]
    )

    kwargs = fake_client.messages.create.call_args.kwargs
    assert "system" in kwargs
    assert all(m["role"] != "system" for m in kwargs["messages"])


def test_chat_returns_tool_calls_in_unified_shape(monkeypatch):
    """LLMResponse.tool_calls should match the OpenAI-style shape the
    rest of the agent expects, even though Claude sent tool_use blocks."""
    provider, fake_client = _make_provider(monkeypatch)
    fake_client.messages.create.return_value = _fake_anthropic_response(
        text="I'll call file_ops.",
        tool_uses=[("toolu_x", "file_ops", {"action": "list"})],
    )

    resp = provider.chat([Message(role="user", content="list files")])
    assert resp.content == "I'll call file_ops."
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["id"] == "toolu_x"
    assert resp.tool_calls[0]["function"]["name"] == "file_ops"
    assert resp.tool_calls[0]["function"]["arguments"] == {"action": "list"}


def test_chat_surfaces_cache_token_savings(monkeypatch):
    provider, fake_client = _make_provider(monkeypatch)
    fake_client.messages.create.return_value = _fake_anthropic_response(
        "ok",
        usage={"input": 50, "output": 10, "cache_read": 2500, "cache_creation": 0},
    )
    resp = provider.chat([Message(role="user", content="hi")])
    assert resp.usage["cache_read"] == 2500
    assert resp.usage["input"] == 50


def test_chat_raises_on_sdk_error(monkeypatch):
    provider, fake_client = _make_provider(monkeypatch)
    fake_client.messages.create.side_effect = RuntimeError("429 rate limit")
    with pytest.raises(RuntimeError, match="rate limit"):
        provider.chat([Message(role="user", content="hi")])


# ─── Streaming ────────────────────────────────────────────────────────


def test_stream_yields_text_chunks(monkeypatch):
    provider, fake_client = _make_provider(monkeypatch)

    class _FakeStream:
        text_stream = iter(["Hel", "lo ", "world"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_client.messages.stream.return_value = _FakeStream()

    chunks = list(provider.stream([Message(role="user", content="hi")]))
    assert chunks == ["Hel", "lo ", "world"]


# ─── Integration: multi-turn tool round-trip ──────────────────────────


def test_full_multi_turn_round_trip(monkeypatch):
    """Simulate one full agent loop tick: system -> user -> assistant w/
    tool_use -> tool result -> assistant text. Confirms the conversion
    produces exactly the message shape Claude expects throughout."""
    provider, fake_client = _make_provider(monkeypatch)
    fake_client.messages.create.return_value = _fake_anthropic_response("Done.")

    history = [
        Message(role="system", content="You are OpenBro."),
        Message(role="user", content="kya time hua"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {"id": "t1", "function": {"name": "datetime", "arguments": {"format": "now"}}}
            ],
        ),
        Message(role="tool", content="2026-05-29 14:30", tool_call_id="t1"),
    ]
    provider.chat(history)

    kwargs = fake_client.messages.create.call_args.kwargs
    msgs = kwargs["messages"]
    # 1: user, 2: assistant (tool_use block), 3: user (tool_result block)
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "kya time hua"
    assert msgs[1]["role"] == "assistant"
    assert any(b["type"] == "tool_use" for b in msgs[1]["content"])
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["content"] == "2026-05-29 14:30"
