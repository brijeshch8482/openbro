"""Tests for SemanticMemory."""

import pytest

from openbro.brain.memory import SemanticMemory


@pytest.fixture
def mem(tmp_path):
    return SemanticMemory(tmp_path / "mem.db")


def test_add_returns_id(mem):
    rid = mem.add("hello world")
    assert rid > 0


def test_add_empty_returns_minus_one(mem):
    assert mem.add("") == -1
    assert mem.add("   ") == -1


def test_count_after_add(mem):
    mem.add("first")
    mem.add("second")
    assert mem.count() == 2


def test_search_keyword_fallback_finds_substring(mem):
    """Without sentence-transformers, falls back to keyword scoring."""
    mem.add("Mai Python script likh raha tha")
    mem.add("Aaj weather Mumbai me bahut accha hai")
    mem.add("Random unrelated text")
    hits = mem.search("Python script")
    assert len(hits) >= 1
    assert any("Python" in h["text"] for h in hits)


def test_search_returns_empty_for_no_match(mem):
    mem.add("hello")
    hits = mem.search("completely unrelated jibberish xyzqq")
    assert hits == []


def test_search_kind_filter(mem):
    mem.add("user message about cats", kind="user")
    mem.add("assistant reply about cats", kind="assistant")
    hits_user = mem.search("cats", kind="user")
    assert all(h["kind"] == "user" for h in hits_user)


def test_compact_drops_old_entries(mem):
    import sqlite3
    import time

    mem.add("recent")
    # Manually backdate one entry
    with sqlite3.connect(mem.db_path) as con:
        con.execute(
            "INSERT INTO memory (ts, kind, text, meta, embedding) VALUES (?, ?, ?, ?, ?)",
            (time.time() - 365 * 86400, "user", "very old", "{}", None),
        )
    removed = mem.compact(keep_recent_days=180)
    assert removed >= 1
    assert mem.count() == 1


def test_context_for_returns_empty_on_no_hits(mem):
    assert mem.context_for("nothing here xyz") == ""


def test_context_for_formats_hits(mem):
    mem.add("Reddit scraper banaya tha BeautifulSoup se")
    ctx = mem.context_for("scraper")
    assert "[user]" in ctx
    assert "scraper" in ctx.lower() or "reddit" in ctx.lower()


def test_meta_persists(mem):
    mem.add("hello", meta={"session": "abc", "lang": "en"})
    hits = mem.search("hello")
    assert hits[0]["meta"].get("session") == "abc"
