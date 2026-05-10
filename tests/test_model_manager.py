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


def test_resolve_alias_llama():
    prov, model = _resolve("llama")
    assert prov == "local"
    assert "llama" in model


def test_resolve_alias_mistral():
    prov, model = _resolve("mistral")
    assert prov == "local"
    assert "mistral" in model


def test_resolve_local_explicit_tag():
    prov, model = _resolve("llama3.2:3b")
    assert prov == "local"
    assert model == "llama3.2:3b"


def test_resolve_ollama_alias_routes_to_local():
    """'ollama' kept as back-compat alias; resolves to local."""
    prov, model = _resolve("ollama")
    assert prov == "local"


def test_resolve_claude_model_name():
    prov, model = _resolve("claude-sonnet-4-5")
    assert prov == "anthropic"
    assert model == "claude-sonnet-4-5"


def test_resolve_gpt_model_name():
    prov, model = _resolve("gpt-4o-mini")
    assert prov == "openai"
    assert model == "gpt-4o-mini"


def test_aliases_cover_main_providers():
    providers = {prov for prov, _ in ALIASES.values()}
    assert "anthropic" in providers
    assert "openai" in providers
    assert "groq" in providers
    assert "google" in providers
    assert "local" in providers


def test_remove_local_model_unlinks_file(tmp_path):
    """Removing a local model should delete the GGUF file from disk."""
    from openbro.cli import model_manager

    fake_file = tmp_path / "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    fake_file.write_bytes(b"fake gguf")

    with patch.object(model_manager, "load_config", return_value={"llm": {}}):
        with patch(
            "openbro.utils.local_llm_setup.find_installed_match",
            return_value=fake_file,
        ):
            with patch.object(model_manager.Confirm, "ask", return_value=True):
                ok = model_manager.remove_model("llama-small")
    assert ok is True
    assert not fake_file.exists()


def test_remove_active_model_requires_confirm():
    from openbro.cli import model_manager

    cfg = {"llm": {"provider": "local", "model": "llama3.1:8b"}}
    with patch.object(model_manager, "load_config", return_value=cfg):
        with patch.object(model_manager.Confirm, "ask", return_value=False):
            ok = model_manager.remove_model("llama")
            assert ok is False
