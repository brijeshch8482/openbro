"""Tests for SkillRegistry + Skill matching + execution."""

import pytest

from openbro.brain.skills import SkillRegistry


@pytest.fixture
def registry(tmp_path):
    return SkillRegistry(tmp_path / "skills")


def test_empty_registry_lists_nothing(registry):
    assert registry.list() == []


def test_add_creates_file(registry):
    skill = registry.add(
        name="say_hi",
        code='def run(**kwargs): return "hello"',
        description="Says hi",
        triggers=["hi", "hello"],
    )
    assert skill.path.exists()
    assert "say_hi" in [s.name for s in registry.list()]


def test_add_invalid_name_raises(registry):
    with pytest.raises(ValueError):
        registry.add(name="Bad Name!", code="def run(): pass")


def test_match_by_trigger_keyword(registry):
    registry.add(
        name="greet",
        code='def run(**k): return "hi"',
        triggers=["greet", "namaste"],
    )
    hit = registry.match("namaste bhai")
    assert hit is not None
    assert hit.name == "greet"


def test_match_returns_none_when_no_match(registry):
    registry.add(name="x", code="def run(**k): return None", triggers=["xyz123"])
    assert registry.match("totally different") is None


def test_run_inproc_returns_output(registry):
    registry.add(
        name="echo",
        code='def run(**kwargs): return "echoed: " + kwargs.get("msg", "")',
        triggers=["echo"],
    )
    out = registry.run("echo", msg="hello")
    assert out["ok"] is True
    assert "echoed: hello" in out["output"]


def test_run_unknown_skill_returns_error(registry):
    out = registry.run("does_not_exist")
    assert out["ok"] is False
    assert "unknown skill" in out["error"]


def test_run_failing_skill_increments_fail_count(registry):
    registry.add(
        name="boom",
        code='def run(**k): raise RuntimeError("nope")',
        triggers=["boom"],
    )
    out = registry.run("boom")
    assert out["ok"] is False
    assert registry.get("boom").fail_count == 1


def test_run_success_increments_count(registry):
    registry.add(name="ok_skill", code='def run(**k): return "yes"')
    registry.run("ok_skill")
    registry.run("ok_skill")
    assert registry.get("ok_skill").success_count == 2


def test_remove_deletes_file(registry):
    registry.add(name="temp", code="def run(): pass")
    assert registry.remove("temp") is True
    assert not (registry.dir / "temp.py").exists()
    assert registry.get("temp") is None


def test_remove_unknown_returns_false(registry):
    assert registry.remove("nonexistent") is False


def test_skill_file_format_includes_triggers_and_description(registry):
    registry.add(
        name="formatted",
        code='def run(**k): return ""',
        description="Test description",
        triggers=["test1", "test2"],
    )
    text = (registry.dir / "formatted.py").read_text()
    assert "Test description" in text
    assert "TRIGGERS" in text
    assert "test1" in text


def test_run_subprocess_works(registry):
    """Sandbox mode: subprocess execution."""
    registry.add(
        name="sub_test",
        code='def run(**kwargs): return "from subprocess: " + kwargs.get("x", "")',
        triggers=["sub"],
    )
    out = registry.run("sub_test", sandbox=True, x="hello")
    assert out["ok"] is True
    assert "from subprocess: hello" in out["output"]


def test_match_score_picks_strongest(registry):
    registry.add(name="weak", code="def run(): pass", triggers=["weak_keyword"])
    registry.add(name="strong", code="def run(): pass", triggers=["focus_word"])
    hit = registry.match("I want focus_word right now")
    assert hit.name == "strong"
