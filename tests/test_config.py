"""Tests for configuration system."""

from openbro.utils.config import default_config


def test_default_config_structure():
    config = default_config()
    assert "llm" in config
    assert "providers" in config
    assert "agent" in config
    assert "safety" in config


def test_default_provider_is_local():
    config = default_config()
    assert config["llm"]["provider"] == "local"


def test_all_providers_present():
    config = default_config()
    providers = config["providers"]
    assert "local" in providers
    assert "anthropic" in providers
    assert "openai" in providers
    assert "groq" in providers
    assert "google" in providers
    assert "deepseek" in providers


def test_local_provider_has_path_field():
    config = default_config()
    local = config["providers"]["local"]
    assert "model_path" in local
    assert "n_ctx" in local
    assert "n_gpu_layers" in local


def test_safety_defaults():
    config = default_config()
    assert config["safety"]["confirm_dangerous"] is True
    assert len(config["safety"]["blocked_commands"]) > 0
