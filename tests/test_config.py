"""Tests for configuration system."""

from openbro.utils.config import default_config


def test_default_config_structure():
    config = default_config()
    assert "llm" in config
    assert "providers" in config
    assert "agent" in config
    assert "safety" in config


def test_default_provider_is_ollama():
    config = default_config()
    assert config["llm"]["provider"] == "ollama"


def test_all_providers_present():
    config = default_config()
    providers = config["providers"]
    assert "ollama" in providers
    assert "anthropic" in providers
    assert "openai" in providers
    assert "groq" in providers


def test_safety_defaults():
    config = default_config()
    assert config["safety"]["confirm_dangerous"] is True
    assert len(config["safety"]["blocked_commands"]) > 0
