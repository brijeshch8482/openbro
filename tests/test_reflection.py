"""Tests for Reflector + multi_role + sign_in + brain updater."""

from unittest.mock import MagicMock, patch

import pytest

# ─── Reflector ──────────────────────────────────────────────────


@pytest.fixture
def brain(tmp_path, monkeypatch):
    def fake_paths():
        return {"base": tmp_path}

    monkeypatch.setattr("openbro.brain.storage.get_storage_paths", fake_paths)
    from openbro.brain import Brain

    return Brain.load()


def test_reflector_classify_signal_positive():
    from openbro.brain.reflection import Reflector

    assert Reflector._classify_signal("thanks bro perfect") == "positive"
    assert Reflector._classify_signal("haan ji theek hai") == "positive"


def test_reflector_classify_signal_negative():
    from openbro.brain.reflection import Reflector

    assert Reflector._classify_signal("nahi samjha fir se kar") == "negative"
    assert Reflector._classify_signal("that's not right") == "negative"
    assert Reflector._classify_signal("retry please") == "negative"


def test_reflector_classify_signal_neutral():
    from openbro.brain.reflection import Reflector

    assert Reflector._classify_signal("") == "neutral"
    assert Reflector._classify_signal("ok do it") == "neutral"


def test_reflector_reflect_positive_signal_boosts_skill(brain):
    from openbro.brain.reflection import Reflector

    r = Reflector(brain).reflect(
        prompt="open chrome",
        response="chrome opened",
        used_skill="open_chrome",
        followup="thanks bhai perfect",
    )
    assert r.signal == "positive"
    assert r.confidence_delta > 0
    # Confidence stored in meta
    confs = brain.storage.read_meta().get("skill_confidence", {})
    assert confs.get("open_chrome", 0) > 0.5


def test_reflector_reflect_negative_signal_drops_skill(brain):
    from openbro.brain.reflection import Reflector

    Reflector(brain).reflect(
        prompt="run script",
        response="ran",
        used_skill="my_script",
        followup="nahi galat hua",
    )
    confs = brain.storage.read_meta().get("skill_confidence", {})
    assert confs.get("my_script", 0.5) < 0.5


def test_reflector_detects_project_mention(brain):
    from openbro.brain.reflection import Reflector

    Reflector(brain).reflect(
        prompt="mera openbro project me ye add kar",
        response="done",
    )
    project_names = [p.name for p in brain.profile.projects]
    assert "openbro" in project_names


def test_reflector_extract_patterns(brain):
    from openbro.brain.reflection import Reflector

    r = Reflector(brain)
    for _ in range(3):
        r.reflect(prompt="x", response="y", used_skill="repeated_skill")
    patterns = r.extract_patterns(window=10)
    assert any(p["skill"] == "repeated_skill" and p["uses"] >= 3 for p in patterns)


def test_compact_brain_decays_confidence(brain):
    from openbro.brain.reflection import compact_brain

    # Set extreme confidences
    brain.storage.update_meta(skill_confidence={"a": 1.0, "b": 0.0})
    summary = compact_brain(brain)
    assert summary["confidence_decayed"] == 2
    confs = brain.storage.read_meta()["skill_confidence"]
    # 1.0 should now be slightly lower (toward 0.5)
    assert confs["a"] < 1.0
    # 0.0 should be slightly higher
    assert confs["b"] > 0.0


# ─── Multi-role pipeline ────────────────────────────────────────


def test_needs_planning_simple_returns_false():
    from openbro.core.multi_role import needs_planning

    assert needs_planning("hi") is False
    assert needs_planning("aaj kya date hai?") is False


def test_needs_planning_complex_returns_true():
    from openbro.core.multi_role import needs_planning

    assert needs_planning("first read the file, then refactor it, finally run the tests") is True


def test_plan_returns_steps_from_json():
    from openbro.core.multi_role import plan
    from openbro.llm.base import LLMResponse

    fake_llm = MagicMock()
    fake_llm.chat.return_value = LLMResponse(
        content='["read main.py", "fix typo", "run tests"]', tool_calls=[]
    )
    steps = plan(fake_llm, "fix the typo and run tests")
    assert steps == ["read main.py", "fix typo", "run tests"]


def test_plan_handles_markdown_fences():
    from openbro.core.multi_role import plan
    from openbro.llm.base import LLMResponse

    fake_llm = MagicMock()
    fake_llm.chat.return_value = LLMResponse(content='```json\n["a", "b"]\n```', tool_calls=[])
    assert plan(fake_llm, "do something") == ["a", "b"]


def test_plan_returns_empty_on_invalid_json():
    from openbro.core.multi_role import plan
    from openbro.llm.base import LLMResponse

    fake_llm = MagicMock()
    fake_llm.chat.return_value = LLMResponse(content="not json at all", tool_calls=[])
    assert plan(fake_llm, "x") == []


def test_verify_returns_yes():
    from openbro.core.multi_role import verify
    from openbro.llm.base import LLMResponse

    fake_llm = MagicMock()
    fake_llm.chat.return_value = LLMResponse(content="yes: looks correct", tool_calls=[])
    ok, note = verify(fake_llm, ["step 1"], "did step 1")
    assert ok is True


def test_verify_returns_no():
    from openbro.core.multi_role import verify
    from openbro.llm.base import LLMResponse

    fake_llm = MagicMock()
    fake_llm.chat.return_value = LLMResponse(content="no: missing step", tool_calls=[])
    ok, note = verify(fake_llm, ["a", "b"], "incomplete")
    assert ok is False


# ─── Sign-in ────────────────────────────────────────────────────


def test_sign_in_aider_uses_env_var():
    from openbro.orchestration.sign_in import is_signed_in

    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
        signed, _ = is_signed_in("aider")
    assert signed is True


def test_sign_in_aider_no_env_var(monkeypatch):
    from openbro.orchestration.sign_in import is_signed_in

    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    signed, reason = is_signed_in("aider")
    assert signed is False
    assert "env var" in reason


def test_ensure_signed_in_returns_not_installed_for_missing_cli():
    from openbro.orchestration.sign_in import ensure_signed_in

    with patch("openbro.orchestration.sign_in.shutil.which", return_value=None):
        result = ensure_signed_in("claude")
    assert result["ready"] is False
    assert "not installed" in result["message"]


def test_ensure_signed_in_signs_in_when_already_authenticated():
    from openbro.orchestration.sign_in import ensure_signed_in

    fake_proc = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("openbro.orchestration.sign_in.shutil.which", return_value="/usr/bin/claude"):
        with patch("openbro.orchestration.sign_in.subprocess.run", return_value=fake_proc):
            result = ensure_signed_in("claude")
    assert result["ready"] is True


def test_ensure_signed_in_detects_auth_error():
    from openbro.orchestration.sign_in import ensure_signed_in

    fake_proc = MagicMock(returncode=1, stdout="", stderr="not authenticated")
    with patch("openbro.orchestration.sign_in.shutil.which", return_value="/usr/bin/claude"):
        with patch("openbro.orchestration.sign_in.subprocess.run", return_value=fake_proc):
            with patch("openbro.orchestration.sign_in.subprocess.Popen") as fake_popen:
                fake_popen.return_value = MagicMock()
                result = ensure_signed_in("claude")
    assert result["ready"] is False
    assert "sign-in" in result["message"].lower() or "login" in result["message"].lower()


# ─── Brain updater (community sync) ─────────────────────────────


def test_fetch_community_brain_offline_returns_none():
    from openbro.brain.updater import fetch_community_brain

    with patch("openbro.brain.updater.httpx.get") as mock_get:
        import httpx

        mock_get.side_effect = httpx.ConnectError("no internet")
        result = fetch_community_brain()
    assert result is None


def test_fetch_community_brain_404_returns_none():
    from openbro.brain.updater import fetch_community_brain

    fake_resp = MagicMock(status_code=404)
    with patch("openbro.brain.updater.httpx.get", return_value=fake_resp):
        result = fetch_community_brain()
    assert result is None


def test_fetch_community_brain_parses_json():
    from openbro.brain.updater import fetch_community_brain

    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"version": "1", "patterns": [], "skills": []}
    with patch("openbro.brain.updater.httpx.get", return_value=fake_resp):
        result = fetch_community_brain()
    assert result == {"version": "1", "patterns": [], "skills": []}


def test_apply_manifest_updates_model_scores(brain):
    from openbro.brain.updater import apply_manifest

    manifest = {
        "skills": [],
        "patterns": [],
        "model_scores": {"future-model-x": 105, "claude-sonnet": 99},
    }
    summary = apply_manifest(brain, manifest)
    assert summary["model_scores_updated"] == 2
    from openbro.llm.auto_select import CAPABILITY

    assert CAPABILITY.get("future-model-x") == 105


def test_brain_update_no_internet(brain):
    """Brain.update() should return ok=False on offline."""
    with patch("openbro.brain.updater.httpx.get") as mock_get:
        import httpx

        mock_get.side_effect = httpx.ConnectError("offline")
        result = brain.update()
    assert result["ok"] is False


def test_detect_new_releases_finds_higher_version():
    from openbro.brain.updater import detect_new_releases

    cfg = {"providers": {"anthropic": {"api_key": "sk-x"}}}
    with patch(
        "openbro.brain.updater.fetch_anthropic_models",
        return_value=["claude-3-haiku", "claude-5-sonnet", "claude-4-sonnet"],
    ):
        out = detect_new_releases(cfg, ("anthropic", "claude-3-haiku"))
    # Should suggest claude-5 and claude-4 (both > 3)
    versions = [s["model"] for s in out]
    assert any("5" in v for v in versions)
