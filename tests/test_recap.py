"""Tests for recap synthesizer + session memory + groq tool-call lifting
+ fallback provider behavior.

Fabrication-detector, OpenAppPlaybook, and trim-for-local-swap tests
were dropped in the 2026-05-31 LLM-first refactor; their underlying
modules / methods are gone. Decompose tests were dropped in Phase B1
of the same refactor. What remains here is the persistent
infrastructure (memory, recap, groq provider quirks, fallback
provider) that the new LLM-first agent loop still depends on.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from openbro.core.recap import Recap, build_recap
from openbro.llm.base import Message

# ─── Recap synthesizer ─────────────────────────────────────────────────


def test_recap_empty_history():
    recap = build_recap([])
    assert recap.is_empty()
    assert recap.turns_scanned == 0


def test_recap_finds_goal_setting_user_turn():
    history = [
        Message(role="system", content="system prompt"),
        Message(role="user", content="hello there"),
        Message(role="assistant", content="hi"),
        Message(role="user", content="let's improve the battery backup logic"),
        Message(role="assistant", content="OK, where do I start?"),
    ]
    recap = build_recap(history)
    assert "battery backup" in recap.goal.lower()


def test_recap_falls_back_to_first_user_turn():
    history = [
        Message(role="user", content="quick question about a config"),
        Message(role="assistant", content="sure"),
    ]
    recap = build_recap(history)
    assert "config" in recap.goal.lower()


def test_recap_status_picks_recent_success():
    history = [
        Message(role="user", content="add docker support"),
        Message(role="assistant", content="Done. Dockerfile committed."),
    ]
    recap = build_recap(history)
    assert "✓" in recap.status


def test_recap_status_flags_failure():
    history = [
        Message(role="user", content="let's deploy"),
        Message(role="assistant", content="Build failed: missing env var."),
    ]
    recap = build_recap(history)
    assert "⚠" in recap.status


def test_recap_extracts_next_step_from_last_assistant():
    history = [
        Message(role="user", content="let's improve battery"),
        Message(
            role="assistant",
            content=(
                "Built and installed. Next: unplug charger and run "
                "discharge test to verify backup time."
            ),
        ),
    ]
    recap = build_recap(history)
    assert "discharge" in recap.next_step.lower() or "next" in recap.render().lower()


def test_recap_render_uses_separators():
    r = Recap(goal="Improve X", status="✓ built", next_step="run discharge test")
    out = r.render()
    assert "Goal" in out
    assert "Status" in out
    assert "Next" in out
    assert "·" in out


def test_recap_persisted_goal_wins_over_heuristic(monkeypatch):
    """When session_memory has an open goal for the session, recap uses
    it instead of scanning history."""
    from openbro.core import recap as recap_mod
    from openbro.core import session_memory

    class _FakeGoal:
        text = "Persisted goal from earlier session"

    monkeypatch.setattr(
        session_memory,
        "open_goals",
        lambda session_id, limit=1: [_FakeGoal()],
    )

    history = [
        Message(role="user", content="let's fix the recent bug"),
    ]
    r = recap_mod.build_recap(history, session_id="abc123")
    assert "Persisted goal" in r.goal


# ─── Session memory ───────────────────────────────────────────────────


@pytest.fixture
def tmp_db(monkeypatch):
    """Point session_memory at a throwaway DB for each test."""
    from openbro.core import session_memory

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "memory.db")

        def fake_path():
            from pathlib import Path

            p = Path(db)
            p.parent.mkdir(parents=True, exist_ok=True)
            return p

        monkeypatch.setattr(session_memory, "_db_path", fake_path)
        yield session_memory


def test_record_goal_persists(tmp_db):
    g = tmp_db.record_goal("sess-1", "user-1", "improve battery backup")
    assert g is not None
    assert "battery backup" in g.text

    goals = tmp_db.open_goals("sess-1")
    assert len(goals) == 1
    assert goals[0].text == "improve battery backup"


def test_record_goal_deduplicates_open_goals(tmp_db):
    tmp_db.record_goal("sess-1", "user-1", "fix the parser")
    tmp_db.record_goal("sess-1", "user-1", "fix the parser")
    goals = tmp_db.open_goals("sess-1")
    assert len(goals) == 1


def test_complete_goal_closes_it(tmp_db):
    tmp_db.record_goal("sess-1", "user-1", "ship the feature")
    assert tmp_db.complete_goal("sess-1", "ship the feature") is True
    assert tmp_db.open_goals("sess-1") == []


def test_recent_goals_across_sessions(tmp_db):
    tmp_db.record_goal("sess-A", "user-1", "old goal A")
    tmp_db.record_goal("sess-B", "user-1", "new goal B")
    out = tmp_db.recent_goals("user-1", limit=5)
    texts = [g.text for g in out]
    assert "old goal A" in texts
    assert "new goal B" in texts


def test_record_milestone_with_kind(tmp_db):
    tmp_db.record_milestone("sess-1", "user-1", "tests passing", kind="success")
    tmp_db.record_milestone("sess-1", "user-1", "deploy broke", kind="failure")
    out = tmp_db.recent_milestones("sess-1")
    kinds = {m.kind for m in out}
    assert "success" in kinds
    assert "failure" in kinds


def test_empty_text_returns_none(tmp_db):
    assert tmp_db.record_goal("sess-1", "user-1", "  ") is None
    assert tmp_db.record_milestone("sess-1", "user-1", "") is None


# ─── Groq provider: bare-form tool call lifting ───────────────────────


def test_groq_provider_extracts_bare_tool_call():
    """The groq provider's `_extract_bare_tool_calls` lifts
    `file_ops{...}` into a real tool_calls entry so the agent
    actually executes the call instead of showing the text."""
    from openbro.llm.groq_provider import _extract_bare_tool_calls

    text = 'file_ops{"action": "read", "path": "D:\\\\MapRadiusKotlin"}'
    calls = _extract_bare_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "file_ops"
    assert calls[0]["function"]["arguments"] == {
        "action": "read",
        "path": "D:\\MapRadiusKotlin",
    }


def test_groq_provider_bare_tool_call_multiple():
    """Multiple bare calls chained in one response are all lifted."""
    from openbro.llm.groq_provider import _extract_bare_tool_calls

    text = (
        'First I will list: file_ops{"action": "list", "path": "D:/foo"} '
        'then read: file_ops{"action": "read", "path": "D:/foo/a.txt"}'
    )
    calls = _extract_bare_tool_calls(text)
    assert len(calls) == 2
    assert all(c["function"]["name"] == "file_ops" for c in calls)


def test_groq_provider_bare_tool_call_ignores_unknown_names():
    """A `something{...}` for an unregistered tool name must NOT be
    lifted — could be a literal map/dict the model is discussing."""
    from openbro.llm.groq_provider import _extract_bare_tool_calls

    text = 'somerandomname{"foo": "bar"}'
    assert _extract_bare_tool_calls(text) == []


def test_groq_provider_bare_tool_call_handles_nested_json():
    """Balanced-brace parser must respect nested objects."""
    from openbro.llm.groq_provider import _extract_bare_tool_calls

    text = 'python{"code": "print({\\"x\\": 1})", "timeout": 5}'
    calls = _extract_bare_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "python"


def test_groq_extracts_paren_form_function_tag():
    """Captured 2026-05-31: model emitted
    `<function=app({"action": "open", "app_name": "Adobe Photoshop"})>`
    Args wrapped in parens. Original parser missed this shape and
    Photoshop never opened."""
    from openbro.llm.groq_provider import _extract_function_tag_calls

    text = '<function=app({"action": "open", "app_name": "Adobe Photoshop"})>'
    calls = _extract_function_tag_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "app"
    assert calls[0]["function"]["arguments"]["action"] == "open"
    assert calls[0]["function"]["arguments"]["app_name"] == "Adobe Photoshop"


# ─── Fallback provider: trim, alternation, chain-exhausted ────────────


def test_fallback_provider_trims_messages_to_fit_local_context():
    """Cloud history of 13K tokens going to a local 8K-context
    provider must be trimmed first. Captured 2026-05-30."""
    from unittest.mock import MagicMock

    from openbro.llm.base import LLMResponse
    from openbro.llm.fallback_provider import FallbackProvider

    primary = MagicMock()
    primary.name.return_value = "groq/llama-3.3-70b-versatile"
    primary.chat.side_effect = Exception("503 Service Unavailable")

    fallback = MagicMock()
    fallback.name.return_value = "local/llama3.2:3b"
    fallback.engine = MagicMock()
    fallback.engine.n_ctx = 8192
    fallback.chat.return_value = LLMResponse(
        content="trimmed answer", usage={"input": 1000, "output": 100}
    )

    fb = FallbackProvider(primary=primary, fallback=fallback)
    messages = [
        Message(role="system", content="you are openbro"),
        Message(role="user", content="A" * 4000),
        Message(role="assistant", content="B" * 4000),
        Message(role="user", content="latest question"),
    ]

    out = fb.chat(messages)
    assert out.content == "trimmed answer"
    sent = fallback.chat.call_args[0][0]
    assert sent[0].role == "system"
    assert any("latest question" in (m.content or "") for m in sent)
    total_chars = sum(len(m.content or "") for m in sent)
    assert total_chars < 30000


def test_fallback_provider_reserves_budget_for_tools_schema():
    """Tools schema eats context too — captured 2026-05-30 trim
    was firing but the schema alone pushed over the limit."""
    from unittest.mock import MagicMock

    from openbro.llm.base import LLMResponse
    from openbro.llm.fallback_provider import FallbackProvider

    primary = MagicMock()
    primary.name.return_value = "groq/x"
    primary.chat.side_effect = Exception("503 unavailable")

    fallback = MagicMock()
    fallback.name.return_value = "local/y"
    fallback.engine = MagicMock()
    fallback.engine.n_ctx = 8192
    fallback.chat.return_value = LLMResponse(content="ok", usage={"input": 1, "output": 1})

    fb = FallbackProvider(primary=primary, fallback=fallback)
    tools = [
        {
            "name": f"tool_{i}",
            "description": "X" * 200,
            "parameters": {"type": "object", "properties": {}},
        }
        for i in range(30)
    ]
    messages = [Message(role="system", content="you are openbro")] + [
        Message(role="user", content="A" * 4000) for _ in range(10)
    ]
    fb.chat(messages, tools=tools)
    sent_msgs = fallback.chat.call_args[0][0]
    total_chars = sum(len(m.content or "") for m in sent_msgs)
    assert total_chars < 25000, total_chars


def test_fallback_chain_exhausted_raises_typed_exception():
    """When both primary AND fallback fail, raise
    _FallbackChainExhausted so the agent can turn it into a calm
    message instead of leaking 'ValueError: Requested tokens
    exceed...' to the user."""
    from unittest.mock import MagicMock

    from openbro.llm.fallback_provider import (
        FallbackProvider,
        _FallbackChainExhausted,
    )

    primary = MagicMock()
    primary.name.return_value = "groq/x"
    primary.chat.side_effect = Exception("503 unavailable")

    fallback = MagicMock()
    fallback.name.return_value = "local/y"
    fallback.engine = MagicMock()
    fallback.engine.n_ctx = 8192
    fallback.chat.side_effect = ValueError("Requested tokens (11000) exceed 8192")

    fb = FallbackProvider(primary=primary, fallback=fallback)
    with pytest.raises(_FallbackChainExhausted) as excinfo:
        fb.chat([Message(role="user", content="x")])
    assert "groq/x" in str(excinfo.value)
    assert "local/y" in str(excinfo.value)


def test_friendly_error_handles_fallback_chain_exhausted():
    """The agent's _friendly_error must catch _FallbackChainExhausted
    and produce a calm Hinglish message — not a raw ValueError."""
    from openbro.core.agent import _friendly_error
    from openbro.llm.fallback_provider import _FallbackChainExhausted

    e = _FallbackChainExhausted(
        primary="groq/llama-3.3",
        primary_error="503 Service Unavailable",
        fallback="local/llama3.2:3b",
        fallback_error="Requested tokens (11293) exceed context window",
    )
    msg = _friendly_error(e)
    assert "ValueError" not in msg, msg
    assert "groq/llama-3.3" in msg
    assert "local/llama3.2:3b" in msg
    assert "/recap" in msg or "ruk" in msg.lower()


def test_fallback_provider_no_trim_when_unknown_context():
    """If the fallback provider doesn't expose n_ctx, don't trim —
    safer than guessing a budget."""
    from unittest.mock import MagicMock

    from openbro.llm.base import LLMResponse
    from openbro.llm.fallback_provider import FallbackProvider

    primary = MagicMock()
    primary.name.return_value = "groq/x"
    primary.chat.side_effect = Exception("rate limit 429")

    class _NoCtxProvider:
        engine = None
        n_ctx = None

        def name(self):
            return "custom/y"

        def chat(self, messages, tools=None):
            self._last = messages
            return LLMResponse(content="ok", usage={"input": 1, "output": 1})

    fallback = _NoCtxProvider()

    fb = FallbackProvider(primary=primary, fallback=fallback)
    big = [Message(role="user", content="X" * 100000)]
    fb.chat(big)
    assert fallback._last == big


# ─── Agent loop: model snapshot/restore around the turn ───────────────


def test_agent_restores_model_at_turn_end():
    """The agent snapshots provider.model at turn start and restores
    it in the finally clause so a mid-turn mutation (e.g. by a
    future hot-swap mechanism) doesn't leak across turns."""
    from unittest.mock import MagicMock, patch

    from openbro.core.agent import Agent
    from openbro.llm.base import LLMResponse

    fake_provider = MagicMock()
    fake_provider.name.return_value = "fake"
    fake_provider.supports_tools.return_value = True
    fake_provider.model = "llama-3.3-70b-versatile"
    fake_provider.chat.return_value = LLMResponse(
        content="answer", usage={"input": 10, "output": 5}
    )

    with patch("openbro.core.agent.create_provider", return_value=fake_provider):
        agent = Agent(interactive=False)
        agent.playbook_registry._playbooks = []
        original = fake_provider.model

        def chat_then_swap(*args, **kwargs):
            fake_provider.model = "meta-llama/llama-4-maverick-17b-128e-instruct"
            return LLMResponse(content="ok", usage={"input": 1, "output": 1})

        fake_provider.chat.side_effect = chat_then_swap
        agent.chat("hello")

    assert fake_provider.model == original


# ─── File ops open directory (kept — file_tool still has the walker) ──


def test_file_ops_open_directory_finds_exe(tmp_path):
    """`file_ops open <dir>` scans for .exe / .lnk inside and
    launches the most likely candidate; refuses honestly when
    none found."""
    from unittest.mock import patch

    from openbro.tools.file_tool import FileTool

    empty = tmp_path / "empty_folder"
    empty.mkdir()
    out = FileTool().run(action="open", path=str(empty))
    assert "folder" in out.lower()
    assert "exe" in out.lower()
    assert "Opened" not in out

    with_exe = tmp_path / "audition"
    with_exe.mkdir()
    (with_exe / "Audition.exe").write_text("fake")
    with (
        patch("openbro.tools.file_tool.platform.system", return_value="Windows"),
        patch("openbro.tools.file_tool.os.startfile", create=True) as fake_start,
    ):
        out2 = FileTool().run(action="open", path=str(with_exe))
    assert "Launched" in out2
    assert "Audition.exe" in out2
    fake_start.assert_called_once()


def test_file_ops_open_directory_deprioritizes_installer_exe(tmp_path):
    """When a folder has both Audition.exe and Setup.exe, prefer
    the app exe over the installer."""
    from unittest.mock import patch

    from openbro.tools.file_tool import FileTool

    d = tmp_path / "app"
    d.mkdir()
    (d / "Setup.exe").write_text("fake")
    (d / "Audition.exe").write_text("fake")
    (d / "Uninstall.exe").write_text("fake")
    with (
        patch("openbro.tools.file_tool.platform.system", return_value="Windows"),
        patch("openbro.tools.file_tool.os.startfile", create=True) as fake_start,
    ):
        FileTool().run(action="open", path=str(d))
    called_with = fake_start.call_args[0][0]
    assert "Audition.exe" in called_with
