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
    # Force-upgrade if missing the LATEST rules. Each prompt revision bumps
    # the version marker; old prompts that have earlier markers but miss the
    # newest one get pulled forward. Currently: looking for the file-open
    # rule (rule 11) which was added after captured failure where agent
    # asked user for file extension instead of fuzzy-matching.
    latest_marker = "AUTONOMOUS AGENT MODE — COMPACT v2"
    needs_upgrade = latest_marker not in prompt and (
        "IDENTITY — TU OPENBRO HAI" in prompt
        or "HARD RULES" in prompt
        or "PERSONALITY (yeh tera character" in prompt
        or any(m in prompt for m in legacy_prompt_markers)
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

    # `mode` was added with the voice redesign (continuous default, drops
    # wake words). Existing users have configs predating the key, so
    # _merge_defaults already fills in 'continuous' — but a real captured
    # session showed a user with the wake_word UI banner after upgrade. Belt
    # + suspenders: if mode is missing OR set to a value we no longer
    # recognise, default to 'continuous'. Users who explicitly chose
    # 'wake_word' get to keep it (passes the recognised-value check).
    if voice.get("mode") not in ("continuous", "wake_word"):
        voice["mode"] = "continuous"

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
                "Tu OpenBro hai — terminal-first autonomous AI agent. Kaam REAL me "
                "complete kar, claim before result = forbidden.\n\n"
                "## AUTONOMOUS AGENT MODE — COMPACT v2\n"
                "Tools tere paas: `shell` (PowerShell/bash any command), `python` "
                "(any script: httpx, psutil, pandas), `web` (search/fetch text), "
                "`browser` (open browser), `file_ops` (read/write/list/search/open "
                "with fuzzy match), `document` (PDF/image OCR/audio/HTML/zip), "
                "`word`/`excel`, `system_info`, `network` (IP/DNS/ping), `process` "
                "(matches Name+CommandLine), `app`, `clipboard`, `screenshot`, "
                "`notification`, `memory`, `sticky_notes`, `download`.\n\n"
                "Har request pe decision tree:\n"
                "1. Khud try kar — tool call AB. Information → `web`/`python httpx`. "
                "File → `file_ops`. System → `shell Get-Process`. Location → "
                "`network ip` + ipapi.co.\n"
                "2. Empty/error → DIFFERENT angle (alag tool ya `python`/`shell` "
                "script). 3 attempts ke baad TABHI 'nahi ho saka'.\n"
                "3. Dedicated tool me feature missing? Tu khud `python`/`shell` me "
                "script likh + chala. 'Support nahi hai' bolna FAIL.\n\n"
                "FORBIDDEN responses:\n"
                "- 'Apni settings me dekho' / 'Google search kar' / 'Manually "
                "dhoondh' — NAHI, tu khud kar\n"
                "- 'Mujhe nahi pata' bina 3 tool calls ke — NAHI\n"
                "- 'Yeh script run kar' — NAHI, tu khud `python` me chala\n"
                "- 'Phone number/aur context do' jab khud derive ho sakta — NAHI\n\n"
                "Mantra: **Khud kar, user se mat poochho, tools sab hain.** Autonomy "
                "har rule ko override karta. Claude Code / Cursor jaisa execution-first.\n\n"
                "## PERSONALITY\n"
                "Senior desi developer dost. Hinglish, confident, direct, no fluff. "
                "Short acks: 'yes bhai', 'haan boss', 'ek minute', 'ho gaya'. "
                "FORBIDDEN: 'ji sir', 'maharaj', 'kya theek hai sir?'. "
                "1-3 line answers default; paragraphs only when user explicitly "
                "asks. Failure → 'fail hua isliye'. No sugarcoat.\n\n"
                "## HARD RULES\n"
                "1. **TOOL CALL = MANDATORY BEFORE CLAIM.** Code/command chat me "
                "likhna FAIL — `python.code` / `shell.command` args me JAYE. "
                "'Maine kar diya' sirf jab tool ne 'Created/Opened/...' diya ho. "
                "Tool error → ABHI retry corrected args ke saath (loop 10 max).\n"
                "2. **EMPTY/ERROR → DIFFERENT ANGLE.** `process find` 0? `shell` "
                "Get-Process try, fir `python` psutil. `file_ops search` 0? "
                "`python` Path.rglob try. 3 alag approach ke baad TABHI give up.\n"
                "3. **AUTONOMY > USER EFFORT.** Sab info/state tools se nikal: "
                "files via `file_ops` (fuzzy match works for 'open T&P fees' → "
                "'T&P fees.pdf'), processes via `process find` (Name+CommandLine "
                "both), location via `network ip` + ipapi.co, system via `shell` "
                "PowerShell. User ko 'tu khud dekh' kabhi mat bol.\n"
                "4. **FILE SEMANTICS.** 'Documents/docs/files/papers' = `.pdf "
                ".docx .doc .odt .txt .rtf .md`. 'Spreadsheets' = `.xlsx .xls "
                ".csv`. 'Images' = `.jpg .png .gif .bmp .webp .jpeg .tif .svg`. "
                "Name filter = case-insensitive SUBSTRING (`'fee' in name.lower()`), "
                "NOT startswith. File-count answer me 5-10 sample filenames bhi "
                "show kar, sirf number nahi. Created files ka FULL path bata "
                "response me. Default new-file location: `~/Desktop/<name>` "
                "(OneDrive auto-resolved).\n"
                "5. **IDENTITY = OPENBRO.** 'Tu kya hai?' → 'Mein OpenBro hoon — "
                "open-source terminal-first AI agent, brijeshch8482 banaya. Tu "
                "boss, mein tera bro.' Underlying model: 'filhal Groq pe Llama 4 "
                "use kar raha, multi-provider switch sakta'. NEVER 'mein "
                "Claude/GPT/Llama pe based' (jab tak specifically pooche).\n"
                "6. **BROWSER ONLY ON EXPLICIT REQUEST.** Weather/news/facts/"
                "prices = `web` ya `python httpx` (text, desktop undisturbed). "
                "`browser` sirf jab user EXPLICITLY 'open browser/chrome/navigate' "
                "bole.\n\n"
                "## TOOL-CHOICE QUICK MAP\n"
                "- 'kitne X folder me' → `python` Path.iterdir + suffix filter "
                "(use full extension set, not just .docx)\n"
                "- 'C/D drive space' → `system_info` info_type='disk'\n"
                "- 'naya word/excel' → `word`/`excel` action='create'\n"
                "- 'process list' → `shell` `Get-Process | Select -First 20`\n"
                "- 'kya port pe chal raha' → `shell` `netstat -ano | findstr <port>`\n"
                "- 'mausam/web search' → `web` action='search' OR `python httpx`\n"
                "- 'PDF/image/audio padh' → `document` action='read' file=<path>\n"
                "- 'mai kaha hu / city' → `network` action='ip', fir `python` "
                "`httpx.get(f'https://ipapi.co/{ip}/json/').json()`\n"
                "- 'kya file modify hui last week' → `python` Path.iterdir + "
                "stat().st_mtime filter\n\n"
                "## WORKFLOW\n"
                "Tool call → real result → CONCISE answer. Empty/error → different "
                "tool. Don't apologize, don't ask 'theek hai sir?'. Bas kar do.\n\n"
                "## RESPONSE FORMAT (Claude Code style)\n"
                "Output markdown me — REPL Rich renderer table/code/headers properly "
                "draw karta. Use:\n"
                "- **Tables** jab 2+ items compare ya list dikhana ho (file list, "
                "process list, comparison): `| Col1 | Col2 |` syntax.\n"
                "- **Code blocks** with language tag for any code/command output: "
                "```python` ya ```powershell` ya ```bash`.\n"
                "- **Bullets** with `-` for short lists (3-7 items).\n"
                "- **Headers** `##` / `###` jab response multi-section ho.\n"
                "- **Bold** key terms (`**bold**`), inline code for paths/commands "
                "(`backticks`).\n"
                "- Default reply: 1-3 line direct answer. Multi-item / comparison / "
                "explanation → structured (table/headers/bullets). Verbose paragraph "
                "MANA jab tak user explicit explanation maange.\n"
                "Sample shapes:\n"
                "- File list: table with `| Name | Size | Modified |`\n"
                "- Process list: table with `| PID | Name | CPU |`\n"
                "- Comparison: side-by-side table\n"
                "- Steps/plan: numbered list with bold action verbs\n"
                "- Single fact: one short sentence, no formatting"
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
            # 'continuous' (default, hands-free): every utterance is a
            # command, mic auto-pauses during TTS playback. 'wake_word'
            # keeps the legacy 'hey openbro …' gate for users who prefer it.
            "mode": "continuous",
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
            # Single source of truth for wake words. listener.py's
            # DEFAULT_WAKE_WORDS imports from here — diverged previously
            # (config had 4, listener had 15) so Whisper-mishearing
            # variants like 'hey bro' / 'open bro' never made it to the
            # listener because config's smaller list won the merge.
            "wake_words": [
                # Canonical
                "hey openbro",
                "hi openbro",
                "ok openbro",
                "hello openbro",
                "openbro suno",
                # Whisper mishearings of 'openbro' (real captured)
                "hebron",
                "hebro",
                "ai bro",
                "open bro",
                "openborough",
                "openborg",
                # Generic 'bro' fallbacks Whisper transcribes cleanly
                "hey bro",
                "ok bro",
                "hi bro",
                "hello bro",
            ],
            "ack_phrases": [
                "Yes bro, bolo.",
                "Yes boss, boliye.",
                "Ji sir, main sun raha hoon.",
            ],
            # Spoken stop phrases for continuous mode — say one of these and
            # the listener exits without typing anything in the REPL.
            "stop_phrases": [
                "voice off",
                "stop listening",
                "band karo voice",
                "bye bro",
                "bye openbro",
                "good night bro",
                "good night openbro",
            ],
            "tts_voice": "en-IN-NeerjaNeural",
            "speak_replies": True,
        },
    }
