"""Configuration management for OpenBro."""

from copy import deepcopy
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
            config = yaml.safe_load(f) or {}
        return _migrate_config(_merge_defaults(default_config(), config))
    return default_config()


def save_config(config: dict):
    config_path = get_config_path()
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def _merge_defaults(defaults: dict, config: dict) -> dict:
    """Return config with any newly added default keys filled in."""
    merged = deepcopy(defaults)

    def apply(base: dict, override: dict) -> dict:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                apply(base[key], value)
            else:
                base[key] = value
        return base

    return apply(merged, config)


def _migrate_config(config: dict) -> dict:
    """Lightweight migrations for old stock OpenBro configs."""
    defaults = default_config()
    agent = config.setdefault("agent", {})
    prompt = str(agent.get("system_prompt") or "")
    legacy_prompt_markers = (
        "ek helpful AI bro",
        "a helpful AI assistant",
        "ek helpful AI assistant",
    )
    if "terminal-first" not in prompt and any(m in prompt for m in legacy_prompt_markers):
        agent["system_prompt"] = defaults["agent"]["system_prompt"]

    voice = config.setdefault("voice", {})
    wake_words = [str(w).lower() for w in (voice.get("wake_words") or [])]
    if wake_words and all("openbro" not in w for w in wake_words):
        voice["wake_words"] = defaults["voice"]["wake_words"]
    else:
        # Merge in any new defaults the user is missing — covers the case where
        # the user already had a short ['hey openbro', 'ok openbro'] list saved
        # before we added Whisper-mishearing variants like 'hebron'/'hebro'.
        # Without this, an existing user keeps the old narrow list forever and
        # voice silently ignores their 'hey bro' that Whisper transcribed as
        # 'hebron'. New ones are appended, user-customized ones preserved.
        default_words = defaults["voice"]["wake_words"]
        existing = set(wake_words)
        merged = list(wake_words)
        for w in default_words:
            if w.lower() not in existing:
                merged.append(w)
        if merged != wake_words:
            voice["wake_words"] = merged

    # Force English STT default if the user's config has language=None and
    # they haven't explicitly chosen another. None makes Whisper guess and
    # routinely emits Japanese/Arabic for quiet English audio.
    if voice.get("stt_language") is None and "stt_language" not in (voice.get("_explicit") or {}):
        voice["stt_language"] = defaults["voice"].get("stt_language", "en")

    return config


def default_config() -> dict:
    return {
        "llm": {
            "provider": "local",
            "model": "llama3.2:3b",
            "fallback_provider": None,
        },
        "providers": {
            "local": {
                # Optional explicit path to a .gguf file. If unset, the router
                # resolves the registered model name (e.g. 'llama3.1:8b') to
                # a path under storage.models_dir.
                "model_path": None,
                "n_ctx": 8192,
                "n_gpu_layers": -1,
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
            "google": {
                "api_key": None,
                "model": "gemini-1.5-flash",
            },
            "deepseek": {
                "api_key": None,
                "model": "deepseek-chat",
            },
        },
        "agent": {
            "system_prompt": (
                "Tu OpenBro hai - ek fast, practical, programmer-style personal "
                "AI agent (Claude Code jaisa). "
                "User ka kaam terminal-first tareeke se complete kar. "
                "Available tools — narrow ones (browser, app, file_ops, "
                "system_info, screenshot, etc.) common cases handle karte. "
                "Lekin **agar woh fit nahi hote**, ya tujhe doubt hai ki tool "
                "complete answer dega ya nahi, to **`python` ya `shell` tool "
                "me khud chota script likh ke run kar**. Don't give up, don't "
                "say 'I can't' — first try writing the smallest correct code. "
                "Examples: 'C drive ka space' → `python` me "
                "`import shutil; print(shutil.disk_usage('C:\\\\'))`; "
                "'kitne PNG hain Desktop pe' → `python` me Path glob; "
                "'process list' → `shell` me `Get-Process`. "
                "Tone personal aur bro wali: 'yes bro', 'yes boss', 'ji sir' "
                "short acknowledgements OK. Fir bhi professional, concise, "
                "precise. Risky/destructive actions: permission rules follow kar."
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
        "mcp": {
            "servers": [
                # Example:
                # {"name": "fs", "command": ["mcp-server-filesystem", "/data"], "enabled": false}
            ],
        },
        "voice": {
            "enabled": True,
            "auto_start": False,  # if true, voice listens by default in REPL
            # small is noticeably better than base for Indian English / Hinglish
            # while still staying usable on normal laptops.
            "stt_model": "small",
            # Force English by default — Whisper with language=None will
            # randomly decide a quiet ambient chunk is Japanese / Chinese /
            # Korean and emit garbage transcripts (real user report: 'レブロ
            # how are you?' when speaking English). Setting language=en
            # locks the decoder to English and stops the drift. Users who
            # want Hindi can set voice.stt_language: hi in config.
            "stt_language": "en",
            "stt_device": "cpu",
            "stt_compute_type": "int8",
            "stt_beam_size": 5,
            "stt_vad_filter": True,
            "chunk_seconds": 8.0,
            "silence_threshold": 0.003,
            "silence_seconds": 0.8,
            "use_cloud_stt": False,
            "cloud_stt_model": "whisper-large-v3-turbo",
            "wake_words": [
                "hey openbro",
                "hi openbro",
                "ok openbro",
                "openbro suno",
            ],
            "ack_phrases": [
                "Yes bro, bolo.",
                "Yes boss, boliye.",
                "Ji sir, main sun raha hoon.",
            ],
            "tts_voice": "en-IN-NeerjaNeural",
            "speak_replies": True,
        },
    }
