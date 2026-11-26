"""Harness piece 3 — memory v2: notes, ranked memory block, OPENBRO.md
instructions, memory tool v2, facts migration, session-end extraction."""

from unittest.mock import MagicMock, patch

import pytest

from openbro.llm.base import LLMResponse
from openbro.memory import MemoryManager
from openbro.memory import store as mem_store
from openbro.memory.instructions import (
    find_project_root,
    instruction_files,
    load_instructions,
    signature,
)
from openbro.memory.notes import FileNoteStore, Note, memory_block, slugify
from openbro.memory.ranking import bm25_scores, tokenize
from openbro.tools.memory_tool import MemoryTool


@pytest.fixture(autouse=True)
def temp_memory_dir(tmp_path, monkeypatch):
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


# ─── ranking ──────────────────────────────────────────────────────────


def test_tokenize_handles_hinglish_and_numbers():
    assert tokenize("Fee receipt INV-2041 kitna tha?") == [
        "fee",
        "receipt",
        "inv",
        "2041",
        "kitna",
        "tha",
    ]


def test_bm25_prefers_rare_matching_terms():
    docs = ["the fee receipt for march", "the the the", "fee"]
    scores = bm25_scores("fee receipt", docs)
    assert scores[0] > scores[2] > scores[1] == 0.0


# ─── notes ────────────────────────────────────────────────────────────


def test_slugify():
    assert slugify("Prefers Short Replies!") == "prefers-short-replies"
    assert slugify("") == "memory"


def test_note_roundtrip_and_upsert_keeps_id(tmp_path):
    st = FileNoteStore("default", root=tmp_path)
    first = st.save(Note(name="City", description="lives in Delhi", body="Delhi"))
    again = st.save(Note(name="city", description="moved to Pune", body="Pune"))
    assert first.id == again.id and first.created == again.created
    got = st.get("CITY")
    assert got.body == "Pune" and got.description == "moved to Pune"
    assert len(st.all()) == 1
    index = (st.location() / "INDEX.md").read_text(encoding="utf-8")
    assert "- city (user) — moved to Pune" in index


def test_note_rejects_bad_type(tmp_path):
    with pytest.raises(ValueError):
        FileNoteStore("default", root=tmp_path).save(Note(name="x", description="y", type="junk"))


def test_owner_isolation(tmp_path):
    a = FileNoteStore("user_a", root=tmp_path)
    b = FileNoteStore("user_b", root=tmp_path)
    a.save(Note(name="name", description="A", body="A"))
    assert b.get("name") is None


def test_hand_edited_bad_file_is_skipped(tmp_path):
    st = FileNoteStore("default", root=tmp_path)
    st.save(Note(name="good", description="ok", body="ok"))
    (st.location() / "broken.md").write_text("no frontmatter here", encoding="utf-8")
    assert [n.name for n in st.all()] == ["good"]


# ─── memory block selection ───────────────────────────────────────────


def test_small_memory_inlines_bodies():
    notes = [Note(name="city", description="where they live", body="Delhi")]
    block = memory_block(notes, "", None)
    assert "## MEMORY" in block and "Delhi" in block


def test_large_memory_uses_index_and_ranks_by_query():
    notes = [Note(name="tone", description="short replies", type="feedback", body="x" * 50)]
    notes += [
        Note(name=f"ref-{i}", description=f"reference {i}", type="reference", body="y" * 400)
        for i in range(30)
    ]
    notes.append(
        Note(
            name="fee-folder",
            description="fee receipts live in D:/fees",
            type="reference",
            body="z",
        )
    )
    block = memory_block(notes, "where are my fee receipts", None, budget_tokens=150)
    lines = block.splitlines()
    assert "tone (feedback)" in lines[1]  # feedback always first
    assert "fee-folder" in lines[2]  # best query match next
    assert "more memories" in block
    assert "y" * 50 not in block  # bodies not inlined


def test_project_scoped_notes(tmp_path):
    notes = [
        Note(name="g", description="global", body="g"),
        Note(name="p", description="proj", body="p", scope=f"project:{tmp_path}"),
    ]
    assert "proj" not in memory_block(notes, "", None)
    assert "proj" in memory_block(notes, "", str(tmp_path))


# ─── manager (old facts API on notes) ─────────────────────────────────


def test_manager_facts_api_on_notes():
    m = MemoryManager()
    m.remember("name", "Brijesh")
    assert m.recall("name") == "Brijesh"
    assert m.notes.get("name").type == "user"
    assert "Brijesh" in m.context_prompt()
    assert m.forget("name") and m.recall("name") is None


def test_facts_migrate_once():
    mem_store.set_fact("color", "blue")
    m = MemoryManager()
    assert m.recall("color") == "blue"
    m.forget("color")
    assert MemoryManager().recall("color") is None  # not re-imported


# ─── memory tool v2 ───────────────────────────────────────────────────


def test_memory_tool_save_read_list_search_delete():
    tool = MemoryTool(MemoryManager())
    out = tool.run(
        action="save",
        name="Prefers short replies",
        description="wants 1-3 line answers",
        type="feedback",
        body="Short replies.\nWhy: said so.\nHow to apply: 1-3 lines.",
    )
    assert "Saved memory 'prefers-short-replies'" in out
    assert "Updated" in tool.run(action="save", name="prefers-short-replies", body="Still short.")
    assert "Still short." in tool.run(action="read", name="prefers-short-replies")
    assert "prefers-short-replies (user)" in tool.run(action="list")
    assert "prefers-short-replies" in tool.run(action="search", query="short replies")
    assert "Deleted" in tool.run(action="delete", name="prefers-short-replies")
    assert "No memory named" in tool.run(action="read", name="prefers-short-replies")


def test_memory_tool_legacy_actions():
    tool = MemoryTool(MemoryManager())
    tool.run(action="remember", key="birthday", value="5 May")
    assert "5 May" in tool.run(action="recall", key="birthday")
    assert "Deleted" in tool.run(action="forget", key="birthday")


def test_memory_tool_search_includes_past_messages():
    m = MemoryManager()
    m.add("user", "Excel wali report kal bhejni hai")
    assert "Past conversation messages" in MemoryTool(m).run(action="search", query="Excel")


def test_memory_tool_validation():
    tool = MemoryTool(MemoryManager())
    assert "needs" in tool.run(action="save", name="x")
    assert "type must be" in tool.run(action="save", name="x", body="y", type="bogus")
    assert "Unknown action" in tool.run(action="explode")


# ─── OPENBRO.md instructions ──────────────────────────────────────────


def test_instruction_files_global_then_project_chain(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "OPENBRO.md").write_text("global rule", encoding="utf-8")
    repo = tmp_path / "repo"
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "OPENBRO.md").write_text("repo rule", encoding="utf-8")
    (sub / "OPENBRO.md").write_text("pkg rule", encoding="utf-8")

    assert find_project_root(sub) == repo.resolve()
    files = instruction_files(sub, cfg)
    assert [f.read_text(encoding="utf-8") for f in files] == [
        "global rule",
        "repo rule",
        "pkg rule",
    ]
    text = load_instructions(files)
    assert text.index("global rule") < text.index("repo rule") < text.index("pkg rule")


def test_instructions_budget_truncates(tmp_path):
    f = tmp_path / "OPENBRO.md"
    f.write_text("a" * 10_000, encoding="utf-8")
    text = load_instructions([f], budget_tokens=100)
    assert "truncated" in text and len(text) < 1000


def test_signature_changes_on_edit(tmp_path):
    f = tmp_path / "OPENBRO.md"
    f.write_text("one", encoding="utf-8")
    before = signature([f])
    import os
    import time

    os.utime(f, (time.time() + 5, time.time() + 5))
    assert signature([f]) != before


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


def test_agent_reminder_carries_relevant_memory():
    agent, provider = _agent([LLMResponse(content="ok")])
    agent.memory.remember("city", "Delhi")
    agent.chat("mausam kaisa hai mere city me")
    sent = provider.chat.call_args.args[0]
    assert "Delhi" in sent[-1].content
    assert "Delhi" not in agent.history[0].content  # not in the stable prompt


def test_agent_system_prompt_has_memory_principle():
    agent, _ = _agent([])
    assert "### 9. Memory" in agent.history[0].content


def test_agent_picks_up_openbro_md(tmp_path, monkeypatch):
    agent, _ = _agent([LLMResponse(content="ok"), LLMResponse(content="ok")])
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    agent.chat("hi")
    assert "always answer in Hinglish" not in agent.history[0].content
    (tmp_path / "OPENBRO.md").write_text("always answer in Hinglish", encoding="utf-8")
    agent.chat("hello again")
    assert "always answer in Hinglish" in agent.history[0].content


def test_end_session_off_by_default():
    agent, provider = _agent([LLMResponse(content="ok")])
    agent.chat("hello")
    calls = provider.chat.call_count
    assert agent.end_session() == []
    assert provider.chat.call_count == calls


def test_end_session_applies_ops(monkeypatch):
    agent, provider = _agent([LLMResponse(content="ok")])
    agent.chat("mera naam Brijesh hai")
    monkeypatch.setattr(agent, "_auto_extract_enabled", lambda: True)
    provider.chat.side_effect = [
        LLMResponse(
            content='Here: [{"op": "save", "name": "user-name", "type": "user", '
            '"description": "name", "body": "Brijesh"}, {"op": "delete", "name": "nope"}]'
        )
    ]
    assert agent.end_session() == ["saved user-name"]
    assert agent.memory.recall("user-name") == "Brijesh"


def test_apply_memory_ops_ignores_garbage():
    agent, _ = _agent([])
    assert agent._apply_memory_ops("no json here") == []
    assert agent._apply_memory_ops("[not valid") == []
    assert agent._apply_memory_ops('[{"op": "save"}]') == []
