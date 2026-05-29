"""Tests for configuration system."""

from openbro.utils.config import _merge_defaults, _migrate_config, default_config


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


def test_config_migration_updates_legacy_voice_and_prompt():
    legacy = {
        "agent": {
            "system_prompt": (
                "Tu OpenBro hai - ek helpful AI bro. Friendly aur casual reh, "
                "Hindi-English mix me baat kar. User ki help kar."
            )
        },
        "voice": {"wake_words": ["hey bro", "ok bro"]},
    }
    migrated = _migrate_config(_merge_defaults(default_config(), legacy))
    assert "terminal-first" in migrated["agent"]["system_prompt"]
    assert "hey openbro" in migrated["voice"]["wake_words"]
    assert "ack_phrases" in migrated["voice"]


def test_voice_mode_migration_defaults_to_continuous():
    """Existing users whose configs predate the `mode` key should land on
    continuous after migration — not get stuck in the legacy wake_word UX
    (real captured: user upgraded, REPL still showed 'Voice listening.
    Wake words: ...')."""
    legacy = {"voice": {"wake_words": ["hey openbro"], "auto_start": True}}
    migrated = _migrate_config(_merge_defaults(default_config(), legacy))
    assert migrated["voice"]["mode"] == "continuous"


def test_voice_mode_migration_preserves_explicit_wake_word():
    """If a user has explicitly chosen wake_word mode, migration must not
    silently switch them back."""
    legacy = {"voice": {"mode": "wake_word"}}
    migrated = _migrate_config(_merge_defaults(default_config(), legacy))
    assert migrated["voice"]["mode"] == "wake_word"


def test_voice_mode_migration_resets_unknown_value():
    """A garbage mode value (typo, hand-edited) gets reset to continuous
    rather than crashing the listener constructor."""
    legacy = {"voice": {"mode": "potato"}}
    migrated = _migrate_config(_merge_defaults(default_config(), legacy))
    assert migrated["voice"]["mode"] == "continuous"
