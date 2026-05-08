"""Tests for the OpenBro Brain v2 foundation."""

import json

import pytest
import yaml

from openbro.brain import Brain
from openbro.brain.profile import LanguageStats, UserProfile
from openbro.brain.storage import BRAIN_VERSION, BrainStorage


@pytest.fixture
def brain_dir(tmp_path, monkeypatch):
    """Redirect brain storage to a temp dir for isolation."""

    def fake_paths():
        return {"base": tmp_path, "memory": tmp_path / "memory", "history": tmp_path / "h"}

    monkeypatch.setattr("openbro.brain.storage.get_storage_paths", fake_paths)
    return tmp_path / "brain"


# ─── Storage ────────────────────────────────────────────────────


def test_storage_creates_layout(brain_dir):
    s = BrainStorage()
    assert s.dir.exists()
    assert s.skills_dir.exists()
    assert s.meta_path.exists()


def test_storage_meta_has_brain_id(brain_dir):
    s = BrainStorage()
    meta = s.read_meta()
    assert meta["version"] == BRAIN_VERSION
    assert meta["brain_id"]
    assert meta["created_at"]


def test_storage_update_meta(brain_dir):
    s = BrainStorage()
    s.update_meta(patterns_count=42, skills_count=3)
    meta = s.read_meta()
    assert meta["patterns_count"] == 42
    assert meta["skills_count"] == 3


def test_storage_append_and_read_learnings(brain_dir):
    s = BrainStorage()
    s.append_learning({"type": "test", "data": "first"})
    s.append_learning({"type": "test", "data": "second"})
    events = s.read_learnings()
    assert len(events) == 2
    assert events[0]["data"] == "first"
    assert events[1]["data"] == "second"
    assert "ts" in events[0]


def test_storage_read_learnings_with_limit(brain_dir):
    s = BrainStorage()
    for i in range(10):
        s.append_learning({"type": "test", "i": i})
    last3 = s.read_learnings(limit=3)
    assert len(last3) == 3
    assert last3[-1]["i"] == 9


def test_storage_total_size(brain_dir):
    s = BrainStorage()
    (s.dir / "test.txt").write_bytes(b"X" * 1000)
    assert s.total_size_bytes() >= 1000


# ─── Profile ────────────────────────────────────────────────────


def test_profile_default():
    p = UserProfile()
    assert p.user_id == "default"
    assert p.language.primary == "hinglish"
    assert p.interaction_count == 0


def test_profile_record_language():
    stats = LanguageStats()
    stats.record("hi")
    stats.record("hi")
    stats.record("en")
    assert stats.primary == "hi"  # 2 hits beats 1
    assert stats.secondary in {"en", "hinglish"}


def test_profile_save_load(tmp_path):
    p = UserProfile(user_id="brijesh")
    p.language.record("hinglish")
    p.expertise = ["python", "ai"]
    p.add_or_touch_project("openbro", type="ai_agent", stack=["python"])

    path = tmp_path / "profile.yaml"
    p.save(path)
    assert path.exists()

    loaded = UserProfile.load(path)
    assert loaded.user_id == "brijesh"
    assert loaded.language.primary in {"hinglish", "hi", "en"}
    assert loaded.expertise == ["python", "ai"]
    assert len(loaded.projects) == 1
    assert loaded.projects[0].name == "openbro"
    assert loaded.projects[0].type == "ai_agent"


def test_profile_load_missing_returns_default(tmp_path):
    p = UserProfile.load(tmp_path / "nonexistent.yaml")
    assert p.user_id == "default"
    assert p.interaction_count == 0


def test_profile_context_snippet_includes_basics():
    p = UserProfile(user_id="bro", expertise=["python"])
    p.add_or_touch_project("openbro", status="active")
    snippet = p.context_snippet()
    assert "bro" in snippet
    assert "openbro" in snippet


def test_profile_add_or_touch_project_idempotent():
    p = UserProfile()
    p.add_or_touch_project("alpha")
    p.add_or_touch_project("alpha")  # second call should NOT duplicate
    assert len(p.projects) == 1


def test_profile_yaml_is_human_readable(tmp_path):
    p = UserProfile(user_id="x")
    p.expertise = ["python"]
    path = tmp_path / "profile.yaml"
    p.save(path)
    content = path.read_text()
    # Should be valid YAML and contain the values
    parsed = yaml.safe_load(content)
    assert parsed["user_id"] == "x"
    assert parsed["expertise"] == ["python"]


# ─── Brain (integration) ────────────────────────────────────────


def test_brain_load_creates_directory(brain_dir):
    brain = Brain.load()
    assert brain.storage.dir.exists()
    assert brain.profile.user_id == "default"


def test_brain_record_interaction_increments_count(brain_dir):
    brain = Brain.load()
    brain.record_interaction(
        prompt="hi", response="hello", language="en", tools_used=[], success=True
    )
    assert brain.profile.interaction_count == 1
    # Reload from disk — should persist
    brain2 = Brain.load()
    assert brain2.profile.interaction_count == 1


def test_brain_record_interaction_updates_language():
    """LanguageStats should track repeated languages."""
    p = UserProfile()
    p.record_interaction(lang="hinglish")
    p.record_interaction(lang="hinglish")
    p.record_interaction(lang="en")
    assert p.language.counts["hinglish"] == 2
    assert p.language.counts["en"] == 1


def test_brain_export_and_import_roundtrip(brain_dir, tmp_path):
    brain = Brain.load()
    brain.profile.user_id = "test_user"
    brain.profile.expertise = ["python", "rust"]
    brain.save()

    archive = tmp_path / "brain_backup.tar.gz"
    brain.export(archive)
    assert archive.exists() and archive.stat().st_size > 0

    # Wipe and re-import into a fresh dir
    import shutil

    new_dir = tmp_path / "fresh_brain"
    new_dir.mkdir()
    new_storage = BrainStorage(new_dir)
    new_brain = Brain(storage=new_storage, profile=UserProfile())
    new_brain.import_from(archive, replace=True)

    assert new_brain.profile.user_id == "test_user"
    assert "rust" in new_brain.profile.expertise

    # Cleanup
    shutil.rmtree(new_dir, ignore_errors=True)


def test_brain_stats_shape(brain_dir):
    brain = Brain.load()
    stats = brain.stats()
    assert stats["version"] == BRAIN_VERSION
    assert "brain_id" in stats
    assert "interaction_count" in stats
    assert stats["skills"] == 0  # no skills yet


def test_brain_update_handles_offline(brain_dir):
    """When the community manifest is unreachable, update() returns ok=False."""
    from unittest.mock import patch

    import httpx

    brain = Brain.load()
    with patch("openbro.brain.updater.httpx.get") as mock_get:
        mock_get.side_effect = httpx.ConnectError("no internet")
        result = brain.update()
    assert result["ok"] is False
    assert "manifest_url" in result


def test_brain_skills_count_reflects_files(brain_dir):
    brain = Brain.load()
    # Drop two fake skill files
    (brain.storage.skills_dir / "skill_a.py").write_text("def run(): pass")
    (brain.storage.skills_dir / "skill_b.py").write_text("def run(): pass")
    # And a non-py file (should be ignored)
    (brain.storage.skills_dir / "README.md").write_text("notes")
    assert brain._skills_count() == 2


def test_brain_meta_persists_across_loads(brain_dir):
    b1 = Brain.load()
    bid = b1.storage.read_meta()["brain_id"]
    b2 = Brain.load()
    assert b2.storage.read_meta()["brain_id"] == bid


def test_storage_corrupted_meta_recovers(brain_dir):
    s = BrainStorage()
    # Corrupt meta.json
    s.meta_path.write_text("not valid json {")
    # Reading should auto-heal
    meta = s.read_meta()
    assert meta["version"] == BRAIN_VERSION
    # And the file should now be valid JSON
    json.loads(s.meta_path.read_text())
