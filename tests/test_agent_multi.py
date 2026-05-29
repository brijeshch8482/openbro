"""Tests for the agent's compound-query handling (_chat_multi)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openbro.core.agent import Agent
from openbro.llm.base import LLMResponse


def _build_agent():
    with patch("openbro.core.agent.create_provider") as fake_create:
        fake_provider = MagicMock()
        fake_provider.name.return_value = "fake"
        fake_provider.supports_tools.return_value = True
        # Default: every LLM call returns plain content matching the
        # user input so tests can identify which sub-query ran.
        fake_provider.chat.side_effect = lambda messages, tools=None: LLMResponse(
            content=f"answer for: {messages[-1].content}",
            usage={"input": 100, "output": 10},
        )
        fake_create.return_value = fake_provider
        agent = Agent(interactive=False)
        # No playbooks: keep _chat_impl in the LLM branch
        agent.playbook_registry._playbooks = []
        return agent, fake_provider


def test_single_intent_goes_through_chat_impl():
    """One sub-query path should never instantiate a TaskList."""
    agent, provider = _build_agent()
    out = agent.chat("mai kaha hu")
    assert "mai kaha hu" in out
    # _chat_impl ran once
    assert provider.chat.call_count == 1


def test_multi_intent_runs_each_subquery_in_order():
    """'X aur Y' should produce two _chat_impl calls in order."""
    agent, provider = _build_agent()
    out = agent.chat("close chrome aur open firefox")
    # Each sub-query's LLM answer should be present
    assert "close chrome" in out
    assert "open firefox" in out
    # Both got their own LLM round-trip
    assert provider.chat.call_count == 2
    # Order: 'close chrome' before 'open firefox'
    chrome_idx = out.find("close chrome")
    firefox_idx = out.find("open firefox")
    assert chrome_idx < firefox_idx


def test_multi_intent_renders_tasklist_header():
    agent, _ = _build_agent()
    out = agent.chat("step a aur step b")
    assert "Plan" in out
    assert "[✓]" in out


def test_multi_intent_handles_three_steps():
    agent, provider = _build_agent()
    out = agent.chat("1. close chrome 2. open vscode 3. read README.md")
    assert provider.chat.call_count == 3
    assert "close chrome" in out
    assert "open vscode" in out
    assert "readme" in out.lower()


def test_multi_intent_continues_after_failed_step(monkeypatch):
    """If sub-query #1 returns a friendly-error response, the agent
    should STILL run sub-query #2 — partial progress beats none."""
    agent, provider = _build_agent()

    call_log = []

    def fake_chat(messages, tools=None):
        text = messages[-1].content
        call_log.append(text)
        if "fail-this" in text:
            return LLMResponse(
                content="⏱️  Rate limit hit ho gaya",
                usage={"input": 0, "output": 0},
            )
        return LLMResponse(content=f"ok: {text}", usage={"input": 1, "output": 1})

    provider.chat.side_effect = fake_chat

    out = agent.chat("fail-this kr aur succeed-this kr")
    # Both steps attempted, second one succeeded
    assert len(call_log) == 2
    assert "succeed-this" in out
    # TaskList shows one ✓ and one ✗
    assert "[✓]" in out
    assert "[✗]" in out


def test_multi_intent_emits_plan_events_on_bus():
    """The REPL renderer hooks plan_started / plan_step_start / etc. —
    confirm the agent actually emits them."""
    agent, _ = _build_agent()
    events = []

    def listener(ev):
        if ev.kind.startswith("plan"):
            events.append(ev.kind)

    unsub = agent.bus.subscribe(listener)
    try:
        agent.chat("close chrome aur open firefox")
    finally:
        unsub()
    assert "plan_started" in events
    assert "plan_step_start" in events
    assert "plan_step_end" in events
    assert "plan_finished" in events
    # Two sub-queries -> two start/end pairs
    assert events.count("plan_step_start") == 2
    assert events.count("plan_step_end") == 2
