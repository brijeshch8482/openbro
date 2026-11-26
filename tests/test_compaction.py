"""Harness piece 2 — lossless transcripts, compaction, resume."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from openbro.llm.base import LLMResponse, Message
from openbro.memory.compaction import (
    SUMMARY_MARKER,
    CompactionConfig,
    Compactor,
    estimate_tokens,
    make_summary_message,
    repair_tool_pairs,
    split_head,
    split_turns,
)

# ─── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def temp_memory_dir(tmp_path, monkeypatch):
    """Redirect memory.db to a temp dir for every test."""

    def fake_paths():
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        return {
            "base": tmp_path,
            "memory": memory_dir,
            "history": tmp_path / "history.txt",
            "logs": tmp_path / "logs",
            "cache": tmp_path / "cache",
            "skills": tmp_path / "skills",
            "models": tmp_path / "models",
        }

    monkeypatch.setattr("openbro.memory.store.get_storage_paths", fake_paths)
    yield tmp_path


_ids = iter(range(10_000))


def _m(role: str, content: str = "", **kw) -> Message:
    return Message(role=role, content=content, id=f"m{next(_ids)}", **kw)


def _turn(i: int, tools: int = 0, tool_size: int = 10) -> list[Message]:
    msgs = [_m("user", f"question {i}")]
    for k in range(tools):
        cid = f"c{i}_{k}"
        msgs.append(
            _m(
                "assistant",
                "",
                tool_calls=[{"id": cid, "function": {"name": "echo", "arguments": {}}}],
            )
        )
        msgs.append(_m("tool", "x" * tool_size, tool_call_id=cid))
    msgs.append(_m("assistant", f"answer {i}"))
    return msgs


def _history(turns: list[list[Message]]) -> list[Message]:
    return [Message(role="system", content="system prompt")] + [m for t in turns for m in t]


def _pairs_ok(history: list[Message]) -> bool:
    """Every tool result follows the assistant message that asked for it."""
    pending: set[str] = set()
    for m in history:
        if m.role == "assistant" and m.tool_calls:
            if pending:
                return False
            pending = {c["id"] for c in m.tool_calls}
        elif m.role == "tool":
            if m.tool_call_id not in pending:
                return False
            pending.discard(m.tool_call_id)
        elif pending:
            return False
    return not pending


def _compactor(summarize=None, **cfg) -> Compactor:
    base = {"max_context_tokens": 1000, "trigger_ratio": 0.75}
    base.update(cfg)
    return Compactor(CompactionConfig(**base), summarize=summarize)


# ─── structure helpers ────────────────────────────────────────────────


def test_split_turns_starts_at_user():
    body = _turn(0, tools=1) + _turn(1)
    turns = split_turns(body)
    assert len(turns) == 2
    assert [t[0].role for t in turns] == ["user", "user"]


def test_split_head_finds_summary():
    hist = [Message(role="system", content="sys"), make_summary_message("old stuff")] + _turn(0)
    head, summary, body = split_head(hist)
    assert head[0].content == "sys"
    assert summary.content.startswith(SUMMARY_MARKER)
    assert body[0].role == "user"


def test_repair_drops_incomplete_calls_and_orphans():
    msgs = [
        _m("user", "q"),
        _m("tool", "orphan", tool_call_id="zzz"),
        _m(
            "assistant",
            "checking",
            tool_calls=[
                {"id": "a", "function": {"name": "echo", "arguments": {}}},
                {"id": "b", "function": {"name": "echo", "arguments": {}}},
            ],
        ),
        _m("tool", "result a", tool_call_id="a"),  # b missing — crash mid-turn
        _m("user", "next"),
    ]
    out = repair_tool_pairs(msgs)
    assert [m.role for m in out] == ["user", "assistant", "user"]
    assert out[1].content == "checking" and not out[1].tool_calls
    assert _pairs_ok(out)


# ─── compactor ────────────────────────────────────────────────────────


def test_under_budget_is_noop():
    assert _compactor().fit(_history([_turn(0), _turn(1)])) is None


def test_stage1_clears_old_tool_output_only():
    turns = [_turn(i, tools=1, tool_size=900) for i in range(5)]
    comp = _compactor(max_context_tokens=1600, tool_output_keep_chars=100)
    result = comp.fit(_history(turns))
    assert result is not None
    assert result.stages == ["clear_tool_output"]
    tools = [m for m in result.history if m.role == "tool"]
    cleared = [m for m in tools if m.content.startswith("[tool output cleared")]
    assert len(cleared) == 2  # the 2 turns older than the last 3
    assert "from echo" in cleared[0].content
    assert all(len(m.content) == 900 for m in tools[2:])
    assert cleared[0].id and cleared[0].tool_call_id
    assert _pairs_ok(result.history)


def test_stage2_summarises_old_turns_keeps_recent():
    calls = []

    def summarize(prior, messages, focus):
        calls.append((prior, messages, focus))
        return "## Goal\n- test things"

    turns = [_turn(i, tools=1, tool_size=200) for i in range(12)]
    comp = _compactor(summarize, max_context_tokens=1200, keep_recent_turns=3)
    result = comp.fit(_history(turns))

    assert "summarise" in result.stages
    assert result.history[0].content == "system prompt"
    assert result.history[1].content.startswith(SUMMARY_MARKER)
    assert "test things" in result.history[1].content
    remaining = [m for m in result.history if m.role == "user"]
    assert len(remaining) <= 3
    assert remaining[-1].content == "question 11"
    assert result.summarised_turns == 12 - len(remaining)
    # summary_upto is the last message folded into the summary
    folded = calls[0][1]
    assert result.summary_upto == folded[-1].id
    assert result.tokens_after < result.tokens_before
    assert _pairs_ok(result.history)


def test_summary_is_rolling():
    seen = {}

    def summarize(prior, messages, focus):
        seen["prior"] = prior
        return "merged"

    hist = [Message(role="system", content="sys"), make_summary_message("earlier facts")]
    hist += [m for i in range(10) for m in _turn(i, tools=1, tool_size=300)]
    result = _compactor(summarize, max_context_tokens=1200).fit(hist)
    assert seen["prior"] == "earlier facts"
    assert sum(1 for m in result.history if m.content.startswith(SUMMARY_MARKER)) == 1


def test_summarizer_failure_uses_fallback():
    def boom(prior, messages, focus):
        raise RuntimeError("rate limited")

    turns = [_turn(i, tools=1, tool_size=300) for i in range(10)]
    result = _compactor(boom, max_context_tokens=1200).fit(_history(turns))
    assert "summarise" in result.stages
    assert "question 0" in result.summary
    assert "automatic summary unavailable" in result.summary


def test_count_trigger_summarises_instead_of_dropping():
    turns = [_turn(i) for i in range(10)]  # 20 small messages
    comp = _compactor(lambda p, m, f: "S", max_context_tokens=100_000, max_messages=8)
    result = comp.fit(_history(turns))
    assert "summarise" in result.stages
    _, summary, body = split_head(result.history)
    assert summary is not None and len(body) <= 8


def test_stage3_shrinks_oversized_current_turn():
    big_turn = _turn(0, tools=10, tool_size=1200)
    comp = _compactor(lambda p, m, f: "S", max_context_tokens=2000)
    result = comp.fit(_history([big_turn]))
    assert "shrink_current_turn" in result.stages
    users = [m for m in result.history if m.role == "user"]
    assert len(users) == 1 and "earlier messages of this task were dropped" in users[0].content
    assert result.history[-1].content == "answer 0"
    assert comp.tokens_over(result.history) is False
    assert _pairs_ok(result.history)


def test_count_trigger_never_shrinks_current_turn():
    turn = _turn(0, tools=10)  # 22 small messages, one turn
    comp = _compactor(max_context_tokens=100_000, max_messages=5)
    assert comp.fit(_history([turn])) is None


def test_force_summarises_everything():
    turns = [_turn(i) for i in range(3)]
    result = _compactor(lambda p, m, f: f"focus={f}").fit(
        _history(turns), force=True, focus="paths"
    )
    assert result.history[1].content.endswith("focus=paths")
    assert len(result.history) == 2


# ─── transcript store ─────────────────────────────────────────────────


def test_transcript_roundtrip_with_tool_calls():
    from openbro.memory.transcript import SQLiteTranscriptStore

    st = SQLiteTranscriptStore()
    call = {"id": "c1", "function": {"name": "echo", "arguments": {"text": "hi"}}}
    msgs = [
        Message(role="user", content="q"),
        Message(role="assistant", content="", tool_calls=[call]),
        Message(role="tool", content="echo: hi", tool_call_id="c1"),
        Message(role="assistant", content="done"),
    ]
    uids = [st.append("s1", "default", "cli", m) for m in msgs]
    assert len(set(uids)) == 4

    loaded = st.load("s1", "default")
    assert loaded.summary is None
    assert [m.role for m in loaded.messages] == ["user", "assistant", "tool", "assistant"]
    assert loaded.messages[1].tool_calls == [call]
    assert loaded.messages[2].tool_call_id == "c1"
    assert [m.id for m in loaded.messages] == uids


def test_transcript_summary_loads_only_newer_messages():
    from openbro.memory.transcript import SQLiteTranscriptStore

    st = SQLiteTranscriptStore()
    uids = [
        st.append("s2", "default", "cli", Message(role="user", content=f"q{i}")) for i in range(5)
    ]
    st.set_summary("s2", "default", "first three done", uids[2])
    loaded = st.load("s2", "default")
    assert loaded.summary == "first three done"
    assert [m.content for m in loaded.messages] == ["q3", "q4"]


def test_transcript_migrates_old_schema(temp_memory_dir):
    db = temp_memory_dir / "memory" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL "
        "DEFAULT 'default', session_id TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'cli', "
        "role TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO conversations (session_id, role, content, timestamp) "
        "VALUES ('old', 'user', 'legacy question', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    from openbro.memory.transcript import SQLiteTranscriptStore

    st = SQLiteTranscriptStore()
    loaded = st.load("old", "default")
    assert [m.content for m in loaded.messages] == ["legacy question"]
    assert loaded.messages[0].id is None and loaded.messages[0].tool_calls == []


def test_remote_backend_not_available_yet():
    from openbro.memory.transcript import build_transcript_store

    with pytest.raises(NotImplementedError):
        build_transcript_store({"storage": {"backend": "remote"}})
    with pytest.raises(ValueError):
        build_transcript_store({"storage": {"backend": "ftp"}})


def test_memory_manager_record_sets_id():
    from openbro.memory import MemoryManager

    mm = MemoryManager(session_id="s3")
    msg = Message(role="user", content="hello")
    uid = mm.record(msg)
    assert msg.id == uid
    mm.save_summary("sum", uid)
    t = mm.load_transcript("s3")
    assert t.summary == "sum" and t.messages == []


# ─── agent integration ────────────────────────────────────────────────


def _agent(replies):
    from openbro.core.agent import Agent

    provider = MagicMock()
    provider.name.return_value = "fake"
    provider.supports_tools.return_value = True
    provider.chat.side_effect = replies
    with patch("openbro.core.agent.create_provider", return_value=provider):
        agent = Agent(interactive=False)
    agent.playbooks_enabled = False
    return agent, provider


def test_agent_records_every_message_including_tools():
    agent, _ = _agent(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    {"id": "t1", "function": {"name": "datetime", "arguments": {"action": "now"}}}
                ],
            ),
            LLMResponse(content="it is late"),
        ]
    )
    agent.chat("kya time hua bhai")
    loaded = agent.memory.transcripts.load(agent.memory.session_id, agent.memory.user_id)
    assert [m.role for m in loaded.messages] == ["user", "assistant", "tool", "assistant"]
    assert loaded.messages[1].tool_calls[0]["function"]["name"] == "datetime"
    assert all(m.id for m in agent.history[1:])


def test_agent_compacts_and_saves_summary():
    def reply(messages, tools=None):
        if "You compact a conversation" in messages[0].content:
            return LLMResponse(content="## Goal\n- summarised")
        return LLMResponse(content="answer " + "y" * 400)

    agent, provider = _agent([])
    provider.chat.side_effect = reply
    system_tokens = estimate_tokens(agent.history)
    agent.compactor = Compactor(
        CompactionConfig(
            max_context_tokens=system_tokens + 500, trigger_ratio=1.0, keep_recent_turns=2
        ),
        summarize=agent._summarize,
    )
    for i in range(8):
        agent.chat(f"question number {i}")
    _, summary, _ = split_head(agent.history)
    assert summary is not None and "summarised" in summary.content
    saved = agent.memory.transcripts.load(agent.memory.session_id, agent.memory.user_id)
    assert saved.summary and "summarised" in saved.summary
    # the summary call was a no-tools call with the summariser prompt
    summary_call = next(
        c
        for c in provider.chat.call_args_list
        if "You compact a conversation" in c.args[0][0].content
    )
    assert summary_call.kwargs["tools"] is None


def test_agent_resume_restores_and_repairs():
    agent, _ = _agent([])
    msgs = [
        Message(role="user", content="old q", id="u1"),
        Message(
            role="assistant",
            content="",
            tool_calls=[{"id": "x", "function": {"name": "echo", "arguments": {}}}],
            id="a1",
        ),  # its result never got saved
        Message(role="user", content="newer q", id="u2"),
        Message(role="assistant", content="newer a", id="a2"),
    ]
    n = agent.resume("we were fixing the fee report", msgs)
    assert n == 3
    assert agent.history[1].content.startswith(SUMMARY_MARKER)
    assert [m.content for m in agent.history[2:]] == ["old q", "newer q", "newer a"]
    assert _pairs_ok(agent.history)


def test_context_report_numbers():
    agent, _ = _agent([LLMResponse(content="ok")])
    agent.chat("hello there")
    r = agent.context_report()
    assert r["turns"] == 1 and r["messages"] == 2
    assert r["total"] == r["system"] + r["summary"] + r["conversation"]
