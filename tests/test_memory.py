"""Tests for memory system (SQLite store + manager)."""

import pytest

from openbro.memory import MemoryManager
from openbro.memory import store as mem_store


@pytest.fixture(autouse=True)
def temp_memory_dir(tmp_path, monkeypatch):
    """Redirect memory storage to a temp directory for each test."""

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
    yield


def test_set_and_get_fact():
    mem_store.set_fact("name", "Brijesh")
    assert mem_store.get_fact("name") == "Brijesh"


def test_get_nonexistent_fact():
    assert mem_store.get_fact("nonexistent") is None


def test_update_fact():
    mem_store.set_fact("color", "blue")
    mem_store.set_fact("color", "red")
    assert mem_store.get_fact("color") == "red"


def test_delete_fact():
    mem_store.set_fact("temp", "value")
    assert mem_store.delete_fact("temp") is True
    assert mem_store.get_fact("temp") is None


def test_delete_nonexistent_fact():
    assert mem_store.delete_fact("nonexistent") is False


def test_list_facts():
    mem_store.set_fact("a", "1")
    mem_store.set_fact("b", "2", category="prefs")
    mem_store.set_fact("c", "3", category="prefs")

    all_facts = mem_store.list_facts()
    assert len(all_facts) == 3

    prefs = mem_store.list_facts(category="prefs")
    assert len(prefs) == 2


def test_search_facts():
    mem_store.set_fact("favorite_color", "blue")
    mem_store.set_fact("hobby", "coding")

    results = mem_store.search_facts("color")
    assert len(results) == 1
    assert results[0]["key"] == "favorite_color"


def test_save_and_get_messages():
    mem_store.save_message("user", "hello", session_id="s1")
    mem_store.save_message("assistant", "hi bro", session_id="s1")

    msgs = mem_store.get_recent_messages(session_id="s1")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_search_messages():
    mem_store.save_message("user", "what is python", session_id="s1")
    mem_store.save_message("assistant", "Python is a language", session_id="s1")

    results = mem_store.search_messages("python")
    assert len(results) == 2


def test_list_sessions():
    mem_store.save_message("user", "msg1", session_id="s1")
    mem_store.save_message("user", "msg2", session_id="s2")

    sessions = mem_store.list_sessions()
    assert len(sessions) == 2
    session_ids = {s["session_id"] for s in sessions}
    assert "s1" in session_ids
    assert "s2" in session_ids


def test_get_stats():
    mem_store.set_fact("k", "v")
    mem_store.save_message("user", "test", session_id="s1")

    stats = mem_store.get_stats()
    assert stats["facts"] == 1
    assert stats["messages"] == 1
    assert stats["sessions"] == 1


def test_manager_remember_recall():
    m = MemoryManager()
    m.remember("name", "Brijesh")
    assert m.recall("name") == "Brijesh"


def test_manager_forget():
    m = MemoryManager()
    m.remember("temp", "value")
    assert m.forget("temp") is True
    assert m.recall("temp") is None


def test_manager_working_memory():
    m = MemoryManager(working_size=5)
    for i in range(10):
        m.add("user", f"msg {i}", persist=False)

    working = m.working()
    assert len(working) == 5
    assert working[-1]["content"] == "msg 9"


def test_manager_session_persistence():
    m = MemoryManager()
    m.add("user", "hello")
    m.add("assistant", "hi")

    history = m.session_history()
    assert len(history) == 2


def test_manager_load_session():
    m1 = MemoryManager()
    sid = m1.session_id
    m1.add("user", "first message")

    m2 = MemoryManager()
    m2.load_session(sid)
    assert len(m2.working()) == 1
    assert m2.working()[0]["content"] == "first message"


def test_manager_search():
    m = MemoryManager()
    m.remember("favorite_food", "biryani")
    m.add("user", "I love biryani")

    results = m.search("biryani")
    assert len(results["facts"]) == 1
    assert len(results["messages"]) == 1


def test_manager_context_prompt_empty():
    m = MemoryManager()
    assert m.context_prompt() == ""


def test_manager_context_prompt_with_facts():
    m = MemoryManager()
    m.remember("name", "Brijesh")
    m.remember("city", "Mumbai")

    prompt = m.context_prompt()
    assert "Brijesh" in prompt
    assert "Mumbai" in prompt


def test_separate_user_isolation():
    mem_store.set_fact("name", "user_a", user_id="user_a")
    mem_store.set_fact("name", "user_b", user_id="user_b")

    assert mem_store.get_fact("name", user_id="user_a") == "user_a"
    assert mem_store.get_fact("name", user_id="user_b") == "user_b"
