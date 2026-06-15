"""Tests for the specialist tree — router rules, DB schema, provider
shape. The heavy ML pieces (adapter swap, training) require CUDA and
live HF caches and are exercised by the runtime, not CI.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Build a throw-away categories DB so tests don't depend on the
    real D:/OpenBro-teting/specialists/categories.db. Uses pytest's
    tmp_path so Windows file-locking is handled properly."""
    path = str(tmp_path / "categories.db")
    monkeypatch.setattr("openbro.specialists.CATEGORIES_DB", path)
    monkeypatch.setattr("openbro.specialists.db.CATEGORIES_DB", path)
    monkeypatch.setattr("openbro.specialists.router.CATEGORIES_DB", path)
    from openbro.specialists.db import init_db

    init_db(path)
    return path


# ─── DB schema + seed ────────────────────────────────────────────────


def test_db_seeds_top_level_and_leaves(temp_db):
    """Seed should produce both root and child categories."""
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM categories")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM categories WHERE parent_id IS NULL")
    top = cur.fetchone()[0]
    assert total > 30
    assert 10 <= top <= 30


def test_db_includes_openbro_tools_and_general(temp_db):
    """Two special slugs are load-bearing for the router fallback +
    OpenBro identity training, so they must always exist."""
    conn = sqlite3.connect(temp_db)
    slugs = {r[0] for r in conn.execute("SELECT slug FROM categories")}
    assert "openbro-tools" in slugs
    assert "general" in slugs


def test_db_all_children_have_real_parent(temp_db):
    """No dangling parent_id references after seeding."""
    conn = sqlite3.connect(temp_db)
    bad = conn.execute(
        """SELECT c.slug FROM categories c
             LEFT JOIN categories p ON c.parent_id = p.id
            WHERE c.parent_id IS NOT NULL AND p.id IS NULL"""
    ).fetchall()
    assert bad == []


def test_db_init_is_idempotent(temp_db):
    """Re-calling init_db on a seeded DB must not double-insert."""
    from openbro.specialists.db import init_db

    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    n1 = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    init_db(temp_db)
    n2 = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    assert n1 == n2


# ─── Router ──────────────────────────────────────────────────────────


def test_router_python_question_routes_to_coding_python(temp_db):
    from openbro.specialists.router import Router

    r = Router(db_path=temp_db)
    res = r.route("How do I reverse a python list?")
    assert res.slug == "coding-python"
    assert res.method == "rule"
    assert res.matched_keyword == "python"


def test_router_openbro_identity_routes_to_tools(temp_db):
    """OpenBro identity prompts should hit the openbro-tools leaf,
    not bleed into 'general' (which would dilute identity training)."""
    from openbro.specialists.router import Router

    r = Router(db_path=temp_db)
    assert r.route("kon ho tum?").slug == "openbro-tools"


def test_router_disk_space_does_not_match_earth_space(temp_db):
    """Captured 2026-06-10: 'mere C drive me space' originally matched
    earth-space because of the lone 'space' keyword. Tightening
    earth-space to 'outer space' and adding 'disk space|drive space'
    to openbro-tools fixed it; the test pins that behaviour."""
    from openbro.specialists.router import Router

    r = Router(db_path=temp_db)
    assert r.route("mere C drive me kitna space hai?").slug == "openbro-tools"


def test_router_returns_general_when_nothing_obvious_matches(temp_db, monkeypatch):
    """If both rule and embedding layers fail, the router still hands
    back a usable RouteResult pointing at 'general'."""
    from openbro.specialists.router import Router

    r = Router(db_path=temp_db)
    # Force the embedding tier to raise so the fallback path runs.
    monkeypatch.setattr(
        r, "_embedding_match", lambda _p: (_ for _ in ()).throw(RuntimeError("no model"))
    )
    res = r.route("xxxxxxxxxxxxxxxxxx")
    assert res.slug == "general"
    assert res.method == "fallback"


def test_router_route_returns_within_a_few_milliseconds(temp_db):
    """Rule routing must be cheap — runtime budget is <10 ms per turn."""
    from openbro.specialists.router import Router

    r = Router(db_path=temp_db)
    res = r.route("How do I fix a docker container that won't restart?")
    assert res.elapsed_ms < 50  # generous; rule path is typically <1 ms


def test_router_adapter_path_walks_to_parent_when_leaf_untrained(temp_db):
    """If the leaf has no adapter but the parent does, return the
    parent's adapter path."""
    import sqlite3 as _sql

    conn = _sql.connect(temp_db)
    conn.execute("UPDATE categories SET adapter_path='/fake/parent' WHERE slug='coding'")
    conn.commit()
    from openbro.specialists.router import Router

    r = Router(db_path=temp_db)
    # coding-python leaf has no adapter, but parent 'coding' does.
    assert r.adapter_path_for("coding-python") == "/fake/parent"


# ─── Provider shape ──────────────────────────────────────────────────


def test_specialist_provider_class_exists_and_advertises_correctly():
    """Don't instantiate (it loads a 720 MB tokenizer) — just confirm
    the LLMProvider contract surface looks right."""
    from openbro.llm.specialist_provider import SpecialistProvider

    # name / supports_tools are class-level enough that we can check
    # via __init__ without actually loading the engine.
    assert hasattr(SpecialistProvider, "name")
    assert hasattr(SpecialistProvider, "chat")
    assert hasattr(SpecialistProvider, "supports_tools")


def test_specialist_provider_registered_in_main_router():
    """openbro.llm.router.create_provider must accept 'specialist'."""
    import inspect

    from openbro.llm import router as llm_router

    src = inspect.getsource(llm_router)
    assert "'specialist'" in src or '"specialist"' in src
