"""Tests for openbro/brain/world.py — static PC facts."""

import json

import pytest

from openbro.brain import world as world_mod


@pytest.fixture
def brain(tmp_path, monkeypatch):
    def fake_paths():
        return {"base": tmp_path}

    monkeypatch.setattr("openbro.brain.storage.get_storage_paths", fake_paths)
    from openbro.brain import Brain

    return Brain.load()


def test_snapshot_has_required_fields():
    snap = world_mod.snapshot()
    assert "captured_at" in snap
    assert "os" in snap
    assert "user" in snap
    assert "paths" in snap
    assert "apps" in snap
    assert isinstance(snap["online"], bool)


def test_snapshot_os_block_populated():
    snap = world_mod.snapshot()
    os_block = snap["os"]
    assert os_block["system"]  # 'Windows' / 'Linux' / 'Darwin'
    assert os_block["machine"]  # arch identifier


def test_snapshot_user_has_name_and_host():
    snap = world_mod.snapshot()
    assert snap["user"]["name"]
    assert snap["user"]["hostname"]


def test_detect_paths_returns_dict_of_existing_paths():
    paths = world_mod.detect_paths()
    # All values returned should exist on disk
    from pathlib import Path

    for p in paths.values():
        assert Path(p).exists()


def test_detect_apps_returns_dict():
    apps = world_mod.detect_apps()
    assert isinstance(apps, dict)
    # At minimum the dict shape should be right
    for v in apps.values():
        assert isinstance(v, str)


def test_refresh_writes_world_json(brain):
    data = world_mod.refresh(brain)
    assert brain.storage.world_path.exists()
    on_disk = json.loads(brain.storage.world_path.read_text())
    assert on_disk["user"]["name"] == data["user"]["name"]


def test_load_uses_cache_if_recent(brain):
    """Two consecutive load() calls within 6h should return identical timestamps."""
    first = world_mod.load(brain)
    second = world_mod.load(brain)
    assert first["captured_at"] == second["captured_at"]


def test_load_refreshes_if_stale(brain):
    """Old world.json should be refreshed."""
    from datetime import datetime, timedelta, timezone

    old_ts = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    brain.storage.world_path.write_text(
        json.dumps(
            {"captured_at": old_ts, "os": {}, "user": {}, "paths": {}, "apps": {}, "online": False}
        )
    )
    fresh = world_mod.load(brain)
    assert fresh["captured_at"] != old_ts


def test_load_handles_corrupted_world_json(brain):
    """Garbage in world.json should not crash; just refresh."""
    brain.storage.world_path.write_text("not json {")
    fresh = world_mod.load(brain)
    assert "captured_at" in fresh


def test_context_snippet_compact():
    snap = world_mod.snapshot()
    s = world_mod.context_snippet(snap)
    assert "User environment" in s
    assert "Online:" in s
    # Should not be huge
    assert len(s) < 2000


def test_context_snippet_empty_world():
    assert world_mod.context_snippet({}) == ""


def test_brain_world_property_lazy_loads(brain):
    """Brain.world should populate on first access and reuse afterwards."""
    w = brain.world
    assert "captured_at" in w
    # Second access returns the same object (cached)
    assert brain.world is w


def test_brain_refresh_world_creates_new_snapshot(brain):
    """refresh_world() should bypass the cache."""
    first = brain.world
    new = brain.refresh_world()
    assert new["captured_at"] >= first["captured_at"]


def test_is_online_doesnt_crash():
    """Just confirm the helper returns a bool without raising."""
    result = world_mod.is_online(timeout=0.1)
    assert isinstance(result, bool)
