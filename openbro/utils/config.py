"""Configuration management for OpenBro."""

from pathlib import Path

import yaml


def get_config_dir() -> Path:
    config_dir = Path.home() / ".openbro"
    config_dir.mkdir(exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    return get_config_dir() / "config.yaml"


def load_config() -> dict:
    config_path = get_config_path()
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return default_config()


def save_config(config: dict):
    config_path = get_config_path()
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def default_config() -> dict:
    return {
        "llm": {
            "provider": "ollama",
            "model": "qwen2.5-coder:7b",
            "fallback_provider": None,
        },
        "providers": {
            "ollama": {
                "base_url": "http://localhost:11434",
            },
            "anthropic": {
                "api_key": None,
                "model": "claude-sonnet-4-20250514",
            },
            "openai": {
                "api_key": None,
                "model": "gpt-4o",
            },
            "groq": {
                "api_key": None,
                "model": "llama-3.3-70b-versatile",
            },
        },
        "agent": {
            "system_prompt": (
                "Tu OpenBro hai - ek helpful AI bro."
                " Friendly aur casual reh, Hindi-English"
                " mix me baat kar. User ki help kar."
            ),
            "max_history": 50,
        },
        "storage": {
            "base_dir": str(Path.home() / ".openbro"),
            "models_dir": str(Path.home() / ".openbro" / "models"),
            "cloud_sync": False,
            "cloud_provider": None,
        },
        "safety": {
            "confirm_dangerous": True,
            "blocked_commands": ["rm -rf /", "format", "del /s /q"],
            "permission_mode": "normal",  # normal | boss | auto
            "cli_agent": {
                "max_cost_per_call_usd": 1.00,
                "daily_budget_usd": 10.00,
                "timeout_seconds": 600,
            },
        },
        "language": {
            "auto_detect": True,
            "default": "hinglish",
        },
        "channels": {
            "telegram": {
                "enabled": False,
                "token": None,
                "allowed_users": [],
            },
        },
        "skills": {
            "github": {"token": None},
            "gmail": {"email": None, "app_password": None},
            "gcal": {"ical_url": None},
            "notion": {"token": None},
        },
        "voice": {
            "enabled": True,
            "auto_start": False,  # if true, voice listens by default in REPL
            "stt_model": "base",
            "wake_words": ["hey bro", "hi bro", "ok bro", "bro suno"],
            "tts_voice": "en-IN-NeerjaNeural",
            "speak_replies": True,
        },
    }
