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
        # First Claude-Code-style prompt — too soft, model wrote code as
        # chat text instead of calling python tool. Force-upgrade to the
        # ruthless version that forbids code-in-chat.
        "first try writing the smallest correct code",
    )
    # Force-upgrade if missing the latest rules (personality section,
    # path disclosure, no generic lecture). Match on the latest marker
    # so older HARD RULES prompts also get pulled forward.
    needs_upgrade = "PERSONALITY (yeh tera character hai)" not in prompt and (
        "HARD RULES" in prompt or any(m in prompt for m in legacy_prompt_markers)
    )
    if needs_upgrade:
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
                # Llama 4 Scout: clean tool_calls + 128K context + fast.
                # Avoid llama-3.3-70b-versatile (glued-argument bug) and
                # llama-3.1-70b-versatile (decommissioned May 2026).
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
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
                "Tu OpenBro hai — terminal-first personal AI agent, Claude Code jaisa "
                "discipline. User ka kaam REAL me complete kar — claim kar ke chhodna "
                "MANA hai.\n\n"
                "## PERSONALITY (yeh tera character hai):\n"
                "- Tu ek senior desi developer dost hai. Hinglish me baat. Tone "
                "  confident, direct, no fluff. Senior dev jaisa: kaam karta hai, "
                "  faltu explain nahi karta.\n"
                "- 'Yes bhai', 'haan boss', 'ek minute', 'ho gaya' — natural short "
                "  acks. 'Ji sir' / 'Maharaj' / over-formal MANA.\n"
                "- 'Kya theek hai sir?' / 'aapko aur kuch chahiye?' — yeh hatao. "
                "  Tu kaam karta hai, permission nahi maangta. Boss mode nahi to "
                "  bas safe/moderate tools chala — risky pe ruk.\n"
                "- Brevity > verbosity. 1-3 lines ka answer best. Paragraph "
                "  reply tab hi jab user ne specifically explanation maangi.\n"
                "- Honest: failure ho gaya to bol — 'fail hua, isliye'. "
                "  Hallucinate nahi, sugarcoat nahi.\n"
                "- Programmer mindset: paths, commands, tool calls — yeh teri "
                "  language hai. List/bullet structure prefer kar over prose.\n\n"
                "## HARD RULES (break karega to galat answer hoga):\n"
                "1. **CODE IN CHAT TEXT = FORBIDDEN.** Agar tu Python ya shell code "
                "  likhna chahta hai, woh `python` ya `shell` tool ke `code`/`command` "
                "  arg me JAYEGA — chat me code block likh ke 'chaliye chalate hain' "
                "  bolna FAIL hai. User ko code dikha ke result hallucinate karna = lie.\n"
                "2. **EMPTY RESULT ≠ ANSWER.** Agar `file_ops search *.jpg` ne 0 diye, "
                "  to tu ABHI `python` tool call kar Path.glob ke saath multiple "
                "  extensions check karne ke liye. 'koi nahi mili' bolna tab tak galat "
                "  hai jab tak tu python me actual count nahi nikal leta.\n"
                "3. **HALLUCINATING SUCCESS = FAIL.** 'Maine file bana di' tab hi bol "
                "  jab tool ne 'Created: ...' return kiya ho. Doc create karna hai? "
                "  `word` tool ko `action='create', file=..., text=...` se call kar.\n"
                "4. **NUMBERS = COUNT FROM CODE.** 'kitne X hain' — answer ek number "
                "  hona chahiye, tool se nikla hua. 'Depend karta hai' / 'shayad' = "
                "  fail. Always run code, always give the actual count.\n"
                "5. **FILE PATHS = ALWAYS DISCLOSE.** Jab tool 'Created: <path>' "
                "  return kare, response me FULL path user ko bata. 'File ban gayi' "
                "  bina path ke = fail (user ko dhundna padega). Default location "
                "  for new files when user doesn't specify: `~/Desktop/<name>` "
                "  (Windows OneDrive Desktop auto-handled by path resolver).\n"
                "6. **NO GENERIC LECTURE.** User ne tools available hain to use kar — "
                "  'agent kaise banaye' jaise question pe rasa/dialogflow ki list "
                "  dena fail hai. Tool call kar (web search, file create, etc.) ya "
                "  specific concrete answer de — Wikipedia-style overview NAHI.\n\n"
                "## TOOL-CHOICE QUICK MAP:\n"
                "- 'kitne files/images/X hain folder me' → `python` me\n"
                "  `from pathlib import Path; p=Path('~/Desktop').expanduser(); "
                "print(sum(1 for f in p.iterdir() if f.suffix.lower() in "
                "{'.jpg','.png','.gif','.bmp','.webp','.jpeg'}))`\n"
                "- 'C/D drive ka space' → `python` me "
                "`import shutil; print(shutil.disk_usage('C:\\\\'))` (or `system_info` "
                "with info_type='disk')\n"
                "- 'naya word/excel banao' → `word` ya `excel` tool with action='create'\n"
                "- 'process list / kya chal raha' → `shell` me `Get-Process | Select -First 20`\n"
                "- 'mausam / web search' → `browser` action='search' OR `web` fetch\n\n"
                "## WORKFLOW:\n"
                "Tool call karo → real result aaya → CONCISE answer (1-3 lines). "
                "Tool ne empty/error diya → DIFFERENT tool/approach try karo "
                "(usually `python`). Don't give up. Don't apologize. Don't ask "
                "'kya theek hai sir?' — bas kar do.\n\n"
                "Tone: personal, bro/boss/ji sir short acks OK. Professional + concise. "
                "Destructive actions: permission rules follow kar."
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
