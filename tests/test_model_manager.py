"""Tests for the unified model manager."""

from unittest.mock import patch

from openbro.cli.model_manager import ALIASES, _resolve


def test_resolve_alias_claude():
    prov, model = _resolve("claude")
    assert prov == "anthropic"
    assert model.startswith("claude")


def test_resolve_alias_gpt():
    prov, model = _resolve("gpt")
    assert prov == "openai"


def test_resolve_alias_qwen():
    prov, model = _resolve("qwen")
    assert prov == "ollama"
    assert "qwen" in model


def test_resolve_ollama_explicit_tag():
    prov, model = _resolve("llama3.2:3b")
    assert prov == "ollama"
    assert model == "llama3.2:3b"


def test_resolve_claude_model_name():
    prov, model = _resolve("claude-sonnet-4-5")
    assert prov == "anthropic"
    assert model == "claude-sonnet-4-5"


def test_resolve_gpt_model_name():
    prov, model = _resolve("gpt-4o-mini")
    assert prov == "openai"
    # gpt-mini alias resolves; bare name maps to provider
    assert model == "gpt-4o-mini"


def test_aliases_cover_main_providers():
    providers = {prov for prov, _ in ALIASES.values()}
    assert "anthropic" in providers
    assert "openai" in providers
    assert "groq" in providers
    assert "ollama" in providers


def test_remove_model_ollama_calls_subprocess():
    from openbro.cli import model_manager

    with patch.object(model_manager, "load_config", return_value={"llm": {}}):
        with patch.object(model_manager.subprocess, "run") as runp:
            runp.return_value.returncode = 0
            ok = model_manager.remove_model("llama3.2:3b")
            assert ok is True
            args = runp.call_args[0][0]
            assert args[0] == "ollama"
            assert args[1] == "rm"
            assert args[2] == "llama3.2:3b"


def test_remove_active_model_requires_confirm():
    from openbro.cli import model_manager

    cfg = {"llm": {"provider": "ollama", "model": "qwen2.5-coder:7b"}}
    with patch.object(model_manager, "load_config", return_value=cfg):
        with patch.object(model_manager.Confirm, "ask", return_value=False):
            ok = model_manager.remove_model("qwen")
            assert ok is False
