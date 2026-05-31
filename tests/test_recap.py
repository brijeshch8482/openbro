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


def test_fabricated_bare_json_tool_args_caught():
    """Captured 2026-05-30: llama-3.3-70b emitted
    `file_ops{"action": "read", "path": "D:\\\\MapRadiusKotlin"}` as
    chat text. The original regex required whitespace between tool
    name and args; the no-space JSON shape leaked through and the
    user saw raw text as the answer instead of debug output."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    text = 'file_ops{"action": "read", "path": "D:\\\\MapRadiusKotlin", "force_ocr": false}'
    reason = detect_fabricated_tool_call(text, tool_calls_made=0)
    assert reason is not None
    assert "tool-args" in reason


def test_groq_provider_extracts_bare_tool_call():
    """Tier-1 fix: the groq provider's `_extract_bare_tool_calls`
    lifts `file_ops{...}` into a real tool_calls entry so the agent
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


def test_fallback_provider_trims_messages_to_fit_local_context():
    """Captured 2026-05-30: 'this is...app so you have to see' →
    Groq 503 → FallbackProvider cascade to local llama3.2:3b →
    ValueError 'requested (10876) exceed context window of 8192'.
    The FallbackProvider must pre-trim cloud history before
    delegating to the local fallback."""
    from unittest.mock import MagicMock

    from openbro.llm.base import LLMResponse, Message
    from openbro.llm.fallback_provider import FallbackProvider

    primary = MagicMock()
    primary.name.return_value = "groq/llama-3.3-70b-versatile"
    primary.chat.side_effect = Exception("503 Service Unavailable")

    fallback = MagicMock()
    fallback.name.return_value = "local/llama3.2:3b"
    # Simulate a llama.cpp engine with 8K context.
    fallback.engine = MagicMock()
    fallback.engine.n_ctx = 8192
    fallback.chat.return_value = LLMResponse(
        content="trimmed answer", usage={"input": 1000, "output": 100}
    )

    fb = FallbackProvider(primary=primary, fallback=fallback)
    # Build a fake 13K-token history (system prompt + big research
    # block + bunch of user/assistant turns).
    messages = [
        Message(role="system", content="you are openbro"),
        Message(role="system", content="[TRANSIENT_RESEARCH] " + "X" * 40000),
        Message(role="user", content="A" * 200),
        Message(role="assistant", content="B" * 200),
        Message(role="user", content="latest question"),
    ]

    out = fb.chat(messages)
    assert out.content == "trimmed answer"
    # The fallback was called with TRIMMED messages — not the full
    # 40K-char research block.
    sent = fallback.chat.call_args[0][0]
    assert not any("[TRANSIENT_RESEARCH]" in (m.content or "") for m in sent)
    # System prompt + recent turns survived.
    assert sent[0].role == "system"
    assert any("latest question" in (m.content or "") for m in sent)
    # Total within budget (8192 - 1500 reserve ≈ 6700 token budget;
    # 4 chars/token ≈ 26.8K chars max).
    total_chars = sum(len(m.content or "") for m in sent)
    assert total_chars < 30000


def test_fallback_provider_reserves_budget_for_tools_schema():
    """Captured 2026-05-30: trim was firing but the tools schema
    (~5-7K tokens for 23 tools) wasn't accounted for in the budget.
    Local llama.cpp counts tools JSON as input, so an 'in-budget'
    history still overflowed when tools were attached. The trim
    must subtract estimated tools-schema cost from the budget."""
    from unittest.mock import MagicMock

    from openbro.llm.base import LLMResponse, Message
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
    # Build a big-ish tools schema (~30 tools).
    tools = [
        {
            "name": f"tool_{i}",
            "description": "X" * 200,
            "parameters": {"type": "object", "properties": {}},
        }
        for i in range(30)
    ]
    # 4K-char history per message × 10 messages = 40K chars.
    messages = [Message(role="system", content="you are openbro")] + [
        Message(role="user", content="A" * 4000) for _ in range(10)
    ]
    fb.chat(messages, tools=tools)
    sent_msgs = fallback.chat.call_args[0][0]
    total_chars = sum(len(m.content or "") for m in sent_msgs)
    # ctx=8192, 1500 response reserve, ~1500 tools reserve → budget
    # ~= 5200 tokens × 4 chars = 20800 chars max. Allow some
    # rounding slack — must be tighter than the no-tools case.
    assert total_chars < 25000, total_chars


def test_fallback_chain_exhausted_raises_typed_exception():
    """When both primary AND fallback fail, raise _FallbackChainExhausted
    so the agent can turn it into a calm message instead of leaking
    'ValueError: Requested tokens exceed...' to the user."""
    from unittest.mock import MagicMock

    import pytest

    from openbro.llm.base import Message
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


def test_fabricated_false_completion_claim_caught():
    """Captured 2026-05-31: user asked 'aaj delhi ka temp kya hai?'.
    Model emitted 'Aaj Delhi ka temperature 28°C hai... Ye data
    maine web tool se fetch kiya hai' with ZERO tool calls. Pure
    fabrication. Then same shape on 'YouTube open kr', 'Photoshop
    open kr'."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    for fake in [
        "Aaj Delhi ka temperature 28°C hai. Ye data maine web tool se fetch kiya hai.",
        "haan boss, YouTube open kar diya hai. Maine browser tool use kiya.",
        "Photoshop khol diya hai, ab aap kaam kar sakte hain!",
        "I've opened Brave and navigated to youtube.com for you.",
        "I called the network tool and the IP is 1.2.3.4.",
        "Successfully launched the application.",
    ]:
        reason = detect_fabricated_tool_call(fake, tool_calls_made=0)
        assert reason is not None, f"should flag completion claim: {fake!r}"
        assert "completion" in reason or "claimed" in reason


def test_fabricated_completion_claim_skipped_when_tool_actually_ran():
    """When the agent DID dispatch a tool this turn, 'kar diya hai'
    is honest reporting — not fabrication."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    text = "Photoshop khol diya hai, ab aap kaam kar sakte hain!"
    assert detect_fabricated_tool_call(text, tool_calls_made=1) is None


def test_fabricated_completion_claim_skipped_for_code_questions():
    """When user asked for code and the response includes code
    fences, claims-of-completion phrasing is part of explaining
    the code — not a tool-execution claim."""
    from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

    text = (
        "Yahan ye snippet hai jo aapka kaam kar dega:\n"
        "```python\nopen(path)\n```\n"
        "Ye function aapke file ko open kar deta hai."
    )
    assert (
        detect_fabricated_tool_call(
            text,
            tool_calls_made=0,
            user_prompt="python me file open karne ka code likh do",
        )
        is None
    )


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


# NOTE: decompose-based tests were removed when the decompose module
# was deleted in Phase B1 of the LLM-first refactor (captured
# 2026-05-31). The agent now hands the full user message to the LLM
# in one turn; the LLM decides whether to emit a plan or answer in
# one shot. The captured edge cases (conversational fragments,
# emotional emphasis, question fragments) are handled by the
# Thinking Principles in the system prompt instead of regex
# heuristics in code.


def test_open_app_playbook_rejects_conversational_target():
    """Captured 2026-05-31: regex matched 'abhi bhi open nhi hua
    hai' with target='nhi hua hai'. App tool tried to launch that
    and reported '✓ Opened: nhi hua hai' as the answer. After the
    fix the playbook must decline → falls through to the LLM."""
    from openbro.playbooks.base import PlaybookContext
    from openbro.playbooks.builtin.open_app import OpenAppPlaybook

    pb = OpenAppPlaybook()
    # Simulate the captured match (regex captured 'nhi hua hai' as
    # target on 'abhi bhi open nhi hua hai').
    ctx = PlaybookContext(
        user_input="abhi bhi open nhi hua hai",
        tool_registry=None,  # type: ignore[arg-type]
        captures={"target": "nhi hua hai"},
    )
    out = pb.execute(ctx)
    assert out == "", f"should decline (empty), got {out!r}"


def test_open_app_playbook_rejects_past_tense_narration():
    """Captured 2026-05-31: 'maine battey backup time pucha hai...
    ki toatal kitne ghante chala hai?' matched the launch verb
    `chala` and captured target = 'maine battey backup time pucha
    hai...ki toatal kitne ghante'. Tool reported '✓ Opened: maine
    battey backup time pucha hai...ki toatal kitne ghante'.
    Bogus."""
    from openbro.playbooks.base import PlaybookContext
    from openbro.playbooks.builtin.open_app import OpenAppPlaybook

    pb = OpenAppPlaybook()
    ctx = PlaybookContext(
        user_input="maine battey backup time pucha hai...ki toatal kitne ghante chala hai?",
        tool_registry=None,  # type: ignore[arg-type]
        captures={"target": "maine battey backup time pucha hai...ki toatal kitne ghante"},
    )
    out = pb.execute(ctx)
    assert out == "", f"should decline (empty), got {out!r}"


def test_open_app_playbook_rejects_long_target():
    """Real app names are 1-3 words. Long captured targets are
    almost certainly sentence content matched accidentally."""
    from openbro.playbooks.base import PlaybookContext
    from openbro.playbooks.builtin.open_app import OpenAppPlaybook

    pb = OpenAppPlaybook()
    ctx = PlaybookContext(
        user_input="open chrome with some really long argument that goes on",
        tool_registry=None,  # type: ignore[arg-type]
        captures={"target": "chrome with some really long argument that goes on"},
    )
    assert pb.execute(ctx) == ""


def test_open_app_playbook_rejects_targets_with_punctuation():
    """Targets containing `?`, `...`, `!`, `,` are sentence content,
    not app names."""
    from openbro.playbooks.base import PlaybookContext
    from openbro.playbooks.builtin.open_app import OpenAppPlaybook

    pb = OpenAppPlaybook()
    for bogus in [
        "chrome?",
        "audio...",
        "browser, please",
        "app!",
    ]:
        ctx = PlaybookContext(
            user_input=f"open {bogus}",
            tool_registry=None,  # type: ignore[arg-type]
            captures={"target": bogus},
        )
        assert pb.execute(ctx) == "", f"should reject: {bogus!r}"


def test_open_app_playbook_still_handles_real_app_names():
    """Regression: real app-name targets still run."""
    from unittest.mock import MagicMock

    from openbro.playbooks.base import PlaybookContext
    from openbro.playbooks.builtin.open_app import OpenAppPlaybook

    pb = OpenAppPlaybook()
    fake_app_tool = MagicMock()
    fake_app_tool.run.return_value = "Opened: chrome.exe"
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = fake_app_tool
    ctx = PlaybookContext(
        user_input="open chrome",
        tool_registry=fake_registry,
        captures={"target": "chrome"},
    )
    out = pb.execute(ctx)
    assert "Opened" in out


def test_file_ops_open_directory_finds_exe(tmp_path):
    """Captured 2026-05-31: file_ops open D:\\softwares\\Adobe Audition
    (a folder) ran os.startfile on the FOLDER and returned 'Opened
    in default app'. File Explorer opened, Audition did not. New
    behaviour: scan the folder for .exe / .lnk and launch the most
    likely candidate; refuse honestly when none found."""
    from unittest.mock import patch

    from openbro.tools.file_tool import FileTool

    # Folder with no .exe inside — must refuse, not silently succeed.
    empty = tmp_path / "empty_folder"
    empty.mkdir()
    out = FileTool().run(action="open", path=str(empty))
    assert "folder" in out.lower()
    assert "exe" in out.lower()
    assert "Opened" not in out

    # Folder with one .exe — must launch it. Pin platform to Windows
    # and stub startfile so the test runs identically on CI (Linux).
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
    called_with = fake_start.call_args[0][0]
    assert "Audition.exe" in called_with


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


# test_decompose_still_splits_real_compound_queries was removed when
# decompose was deleted in Phase B1. Real compound queries are now
# handled by the LLM emitting a numbered plan in its response and
# executing steps via the tool loop, instead of being pre-split by
# regex into separate sub-turns.


def test_agent_restores_model_at_turn_end():
    """Captured 2026-05-30: escalator round 3 swapped Groq model to
    llama-4-maverick mid-turn. The swap was NEVER restored. Every
    subsequent turn ran on maverick → maverick unavailable →
    fallback chain → context overflow. The agent must snapshot the
    provider's model at turn start and restore it in finally."""
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
        # Simulate a mid-turn model swap (as the escalator would do).
        # We can't easily trigger escalator, but the restoration logic
        # is in the outer try/finally so we exercise it directly: mutate
        # model during the call via a chat side-effect that swaps it.
        original = fake_provider.model

        def chat_then_swap(*args, **kwargs):
            fake_provider.model = "meta-llama/llama-4-maverick-17b-128e-instruct"
            return LLMResponse(content="ok", usage={"input": 1, "output": 1})

        fake_provider.chat.side_effect = chat_then_swap
        agent.chat("hello")

    # After the turn ends, the provider's model must be the original.
    assert fake_provider.model == original


def test_fallback_provider_no_trim_when_unknown_context():
    """If the fallback provider doesn't expose n_ctx, don't trim —
    safer than guessing a budget. Captures a custom-provider use
    case where the user wires in a non-llama.cpp backend."""
    from unittest.mock import MagicMock

    from openbro.llm.base import LLMResponse, Message
    from openbro.llm.fallback_provider import FallbackProvider

    primary = MagicMock()
    primary.name.return_value = "groq/x"
    primary.chat.side_effect = Exception("rate limit 429")

    # A provider object without an `engine` attr and without an
    # `n_ctx` attr — represents a custom non-llama.cpp backend.
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
    assert fallback._last == big  # untouched


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
