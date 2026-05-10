"""Tests for the latest-LLM auto-select."""

from unittest.mock import patch

from openbro.llm.auto_select import (
    _capability_for,
    best_available,
    probe_available,
    suggest_upgrade,
)


def test_capability_for_known_models():
    assert _capability_for("llama3.3") >= 90
    assert _capability_for("llama3.2:3b") >= 80
    # Coder variants are downgraded (poor at agent tool calls)
    assert _capability_for("codestral:22b") <= 65


def test_capability_for_unknown_returns_floor():
    assert _capability_for("totally-unknown-model") == 50


def test_probe_returns_at_least_cloud_options():
    """Even with no local model installed, cloud options should appear."""
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        candidates = probe_available()
    providers = {c["provider"] for c in candidates}
    assert "groq" in providers
    assert "anthropic" in providers
    assert "openai" in providers


def test_probe_includes_local_models():
    with patch(
        "openbro.llm.auto_select._local_installed_models",
        return_value=["llama3.1:8b", "phi3:mini"],
    ):
        candidates = probe_available()
    local_models = [c["model"] for c in candidates if c["provider"] == "local"]
    assert "llama3.1:8b" in local_models
    assert "phi3:mini" in local_models


def test_probe_results_sorted_by_score():
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        candidates = probe_available()
    scores = [c["score"] for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_best_available_with_no_keys():
    """Without any API keys and no local model, returns None."""
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        result = best_available({"providers": {}})
    assert result is None


def test_best_available_picks_cloud_when_key_set():
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        cfg = {"providers": {"groq": {"api_key": "gsk_xxx"}}}
        result = best_available(cfg)
    assert result is not None
    assert result["provider"] == "groq"


def test_best_available_prefers_higher_score():
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        cfg = {
            "providers": {
                "groq": {"api_key": "gsk_x"},
                "anthropic": {"api_key": "sk-x"},
            }
        }
        result = best_available(cfg)
    # Anthropic claude-sonnet scores higher than groq llama-3.3
    assert result["provider"] == "anthropic"


def test_suggest_upgrade_returns_none_if_already_best():
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        cfg = {"providers": {"anthropic": {"api_key": "sk"}}}
        # Already on claude-sonnet → no upgrade
        result = suggest_upgrade(("anthropic", "claude-sonnet-4"), cfg)
    assert result is None


def test_suggest_upgrade_offers_better_model():
    with patch("openbro.llm.auto_select._local_installed_models", return_value=[]):
        cfg = {"providers": {"anthropic": {"api_key": "sk"}}}
        # Currently on a weak model, claude-sonnet is much better
        result = suggest_upgrade(("local", "phi3:mini"), cfg)
    assert result is not None
    assert result["score"] > 60
