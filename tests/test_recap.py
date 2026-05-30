"""Tests for recap synthesizer + session memory + reflection retry on
fabricated tool output."""

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


# ─── Fabrication detector ─────────────────────────────────────────────


def test_fabricated_tool_call_detected_when_no_tools_ran():
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    fake = (
        "Let me check the file.\n"
        "```python\n"
        "import os\n"
        "files = os.listdir('D:/desktop')\n"
        "print(files)\n"
        "```\n"
        "\n"
        "Output:\n"
        "```\n"
        "['NDLS_FDB', 'other_file.txt']\n"
        "```\n"
        "The file NDLS_FDB exists on your desktop."
    )
    assert detect_fabricated_tool_call(fake, tool_calls_made=0) is not None


def test_fabricated_tool_call_not_flagged_when_tools_ran():
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    real = "```python\nprint('hi')\n```\nOutput:\n```\nhi\n```"
    # A tool actually ran — the code block is OK as a tool-args render.
    assert detect_fabricated_tool_call(real, tool_calls_made=1) is None


def test_fabricated_tool_call_caught_on_multiple_code_blocks():
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    text = "First I'll do:\n```python\nprint(1)\n```\nThen:\n```shell\nls -la\n```"
    assert detect_fabricated_tool_call(text, tool_calls_made=0) is not None


def test_fabricated_tool_call_single_block_without_fake_output_passes():
    """A single inline code block without a fabricated Output: block is
    NOT flagged — could be a legitimate explanation."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    text = "Here's the call signature:\n```python\nopen(path, 'r')\n```"
    assert detect_fabricated_tool_call(text, tool_calls_made=0) is None


def test_fabricated_rendered_tool_args_caught():
    """Captured: 'iss time mera phone laptop se connected hai ya nhi?'
    → model wrote `network action='ip'` as chat text without making
    the call. Detector catches the rendered-args shape."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    text = (
        "Chal, network tool se apne device ka connection status dekhte hain.\n"
        "\n"
        "Connection Status\n"
        "\n"
        " network action='ip'\n"
    )
    reason = detect_fabricated_tool_call(text, tool_calls_made=0)
    assert reason is not None
    assert "tool-args" in reason


def test_fabricated_promise_without_action_caught():
    """'Let me check X' / 'dekhte hain' with 0 tool calls → fabrication."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    for text in [
        "Let me check the file for you.",
        "I'll run the diagnostic now.",
        "Dekhte hain kya data hai.",
        "Check krta hau quickly.",
    ]:
        reason = detect_fabricated_tool_call(text, tool_calls_made=0)
        assert reason is not None, f"{text!r} should be flagged"
        assert "promised" in reason


def test_rendered_tool_args_skipped_when_real_call_was_made():
    """When at least one tool actually ran, rendered tool args in the
    accompanying chat text are OK (it's just the model echoing the
    call it made)."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    text = "I ran `network action='ip'` for you and got the result."
    assert detect_fabricated_tool_call(text, tool_calls_made=1) is None


# ─── User-asked-for-code skips multi-block fabrication ────────────


def test_multi_code_blocks_allowed_when_user_asked_for_code():
    """Captured failure 2026-05-30: user said 'bro mujhe full
    implementation chahiye to tum full code likho' for kiosk mode.
    Model wrote 4 Java code blocks (Manifest, KioskActivity,
    Configurator, MainActivity) — code IS the answer. Detector
    incorrectly flagged → escalator fired → maverick unavailable →
    local context overflow → user saw an error instead of an
    answer."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    text = (
        "Here's the kiosk mode setup:\n"
        "```java\npublic class A {}\n```\n"
        "And the receiver:\n"
        "```java\npublic class B {}\n```\n"
        "Manifest:\n"
        "```xml\n<receiver/>\n```\n"
    )
    # User explicitly asked for full code.
    assert (
        detect_fabricated_tool_call(text, tool_calls_made=0, user_prompt="bro full code likho")
        is None
    )
    assert (
        detect_fabricated_tool_call(
            text,
            tool_calls_made=0,
            user_prompt="mujhe full implementation chahiye",
        )
        is None
    )
    assert (
        detect_fabricated_tool_call(
            text,
            tool_calls_made=0,
            user_prompt="write me the code please",
        )
        is None
    )
    # WITHOUT user_prompt the old behavior is preserved → still
    # flagged (back-compat for older callers).
    assert detect_fabricated_tool_call(text, tool_calls_made=0) is not None


def test_multi_code_blocks_still_flagged_for_non_code_questions():
    """Even when user_prompt is supplied, multi-code-blocks must still
    fire when the question wasn't a code request — that's the
    original fabrication signal we don't want to silence."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    text = "Let me check.\n```python\nprint(1)\n```\nThen:\n```shell\nls\n```\n"
    reason = detect_fabricated_tool_call(
        text, tool_calls_made=0, user_prompt="what is on my desktop?"
    )
    assert reason is not None


def test_user_asked_for_code_detector_positive_cases():
    """Phrase variants we MUST recognise as a code request."""
    from openbro.playbooks.builtin.tech_research import user_asked_for_code

    for q in [
        "write me a python function",
        "full code likh",
        "code likho please",
        "mujhe full implementation chahiye",
        "give me an example",
        "show me the code",
        "paste the snippet",
        "how do I implement a singleton in Java",
        "complete code de do",
        "full source likh do",
    ]:
        assert user_asked_for_code(q), f"should detect: {q!r}"


def test_user_asked_for_code_detector_negative_cases():
    """Casual questions that mention 'code' or 'implementation' as a
    noun, not as an ask, should not trip the detector."""
    from openbro.playbooks.builtin.tech_research import user_asked_for_code

    for q in [
        "what is python",
        "kya time hua",
        "tell me about django",
        "kiosk mode kya hai",
    ]:
        assert not user_asked_for_code(q), f"should NOT detect: {q!r}"


# ─── History trim before local swap ───────────────────────────────


def test_trim_history_for_local_swap_drops_transient_blocks():
    """Captured failure: cloud-only history of 13K tokens went
    unchanged to local llama3.2:3b → ValueError 'requested (13658) >
    context (8192)'. The trim helper must strip [TRANSIENT_RESEARCH]
    and [TRANSIENT_PLAN] blocks before falling back."""
    from unittest.mock import MagicMock, patch

    from openbro.core.agent import Agent
    from openbro.llm.base import Message

    fake_provider = MagicMock()
    fake_provider.name.return_value = "fake"
    fake_provider.supports_tools.return_value = True

    with patch("openbro.core.agent.create_provider", return_value=fake_provider):
        agent = Agent(interactive=False)
        agent.playbook_registry._playbooks = []
        # Synthetic history: system prompt + huge research block +
        # planning block + a few user/assistant turns.
        agent.history = [
            Message(role="system", content="you are openbro"),
            Message(role="system", content="[TRANSIENT_RESEARCH] " + "X" * 40000),
            Message(role="system", content="[TRANSIENT_PLAN] make a plan"),
            Message(role="user", content="what is the weather"),
            Message(role="assistant", content="sunny"),
        ]
        agent._trim_history_for_local_swap(target_token_budget=2000)

    kinds = [(m.role, (m.content or "")[:30]) for m in agent.history]
    # Transient blocks must be gone.
    assert not any("[TRANSIENT_RESEARCH]" in (m.content or "") for m in agent.history)
    assert not any("[TRANSIENT_PLAN]" in (m.content or "") for m in agent.history)
    # System prompt + recent turns kept.
    assert agent.history[0].role == "system"
    assert any(m.role == "user" for m in agent.history), kinds


def test_trim_history_for_local_swap_keeps_under_budget():
    """The trimmed history must respect the token budget (approx)."""
    from unittest.mock import MagicMock, patch

    from openbro.core.agent import Agent
    from openbro.llm.base import Message

    fake_provider = MagicMock()
    fake_provider.name.return_value = "fake"
    fake_provider.supports_tools.return_value = True

    with patch("openbro.core.agent.create_provider", return_value=fake_provider):
        agent = Agent(interactive=False)
        agent.history = [Message(role="system", content="you are openbro")] + [
            Message(role="user", content="X" * 400) for _ in range(40)
        ]
        agent._trim_history_for_local_swap(target_token_budget=1000)

    # Approx 4 chars per token; total content <= ~4000 chars after trim.
    total_chars = sum(len(m.content or "") for m in agent.history)
    assert total_chars // 4 <= 1100, total_chars  # small margin
