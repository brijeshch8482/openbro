"""Tests for LLM provider system."""

import pytest

from openbro.llm.base import LLMResponse, Message


def test_message_creation():
    msg = Message(role="user", content="hello")
    assert msg.role == "user"
    assert msg.content == "hello"
    assert msg.tool_calls == []


def test_message_with_tool_calls():
    msg = Message(
        role="assistant",
        content="",
        tool_calls=[{"id": "1", "function": {"name": "test"}}],
    )
    assert len(msg.tool_calls) == 1


def test_llm_response():
    resp = LLMResponse(content="hello bro", model="test")
    assert resp.content == "hello bro"
    assert resp.tool_calls == []
    assert resp.usage == {}


def test_router_unknown_provider():
    from openbro.llm.router import create_provider
    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider("nonexistent_provider")
