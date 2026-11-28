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


RETIRED_GROQ_MODELS = frozenset(
    {
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "meta-llama/llama-4-maverick-17b-128e-instruct",
    }
)

STALE_MODEL_SENTENCES = (
    "Underlying model: 'filhal Groq pe Llama 4 use kar raha, multi-provider switch sakta'.",
)
RUNNING_MODEL_SENTENCE = (
    "Underlying model: only the one named in the RUNNING MODEL line of the per-turn "
    "context — never guess."
)
# Older prompts saved new files on the Desktop; now they go where the user
# is working (or the folder they name).
STALE_LOCATION_SENTENCE = "Default new-file location: `~/Desktop/<name>` (OneDrive auto-resolved)."
LOCATION_SENTENCE = (
    "New files go in the folder the user names (create missing folders first); if none "
    "is named, in the current working folder from the workspace context."
)


def _migrate_config(config: dict) -> dict:
    """Lightweight migrations for old stock OpenBro configs."""
    defaults = default_config()
    agent = config.setdefault("agent", {})
    prompt = str(agent.get("system_prompt") or "")
    legacy_prompt_markers = (
        "ek helpful AI bro",
        "a helpful AI assistant",
        "ek helpful AI assistant",
        # An earlier default prompt — too soft, model wrote code as
        # chat text instead of calling python tool. Force-upgrade to the
        # ruthless version that forbids code-in-chat.
        "first try writing the smallest correct code",
        # The earlier Hinglish defaults (stock prompt and wizard personalities);
        # replaced by the professional English prompt.
        "Tu OpenBro hai",
    )
    # Force-upgrade if missing the LATEST rules. Each prompt revision bumps
    # the version marker; old prompts that have earlier markers but miss the
    # newest one get pulled forward. Currently: looking for the file-open
    # rule (rule 11) which was added after captured failure where agent
    # asked user for file extension instead of fuzzy-matching.
    latest_marker = "## PERSONALITY — PROFESSIONAL"
    needs_upgrade = latest_marker not in prompt and (
        "IDENTITY — TU OPENBRO HAI" in prompt
        or "HARD RULES" in prompt
        or "PERSONALITY (yeh tera character" in prompt
        or any(m in prompt for m in legacy_prompt_markers)
    )
    # Groq retired its Llama models; a saved one would 404 on every call.
    groq = (config.get("providers", {}) or {}).get("groq")
    if isinstance(groq, dict) and groq.get("model") in RETIRED_GROQ_MODELS:
        groq["model"] = defaults["providers"]["groq"]["model"]
    llm = config.get("llm", {}) or {}
    if llm.get("provider") == "groq" and llm.get("model") in RETIRED_GROQ_MODELS:
        llm["model"] = defaults["providers"]["groq"]["model"]

    if needs_upgrade:
        agent["system_prompt"] = defaults["agent"]["system_prompt"]
    else:
        # Older prompts hard-coded the model ('Groq pe Llama 4'), so the agent
        # named Groq even after a switch. Replace just that sentence and keep
        # any other edits the user made.
        current = str(agent.get("system_prompt") or "")
        for stale in STALE_MODEL_SENTENCES:
            if stale in current:
                current = current.replace(stale, RUNNING_MODEL_SENTENCE)
                agent["system_prompt"] = current
        if STALE_LOCATION_SENTENCE in current:
            agent["system_prompt"] = current.replace(STALE_LOCATION_SENTENCE, LOCATION_SENTENCE)

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

    # Local provider's own model name. Captured failure: existing user's
    # providers.local block predated the key, fallback tried to lookup
    # llm.model (which holds the GROQ model name) in the local catalogue
    # and crashed startup. Fill the default so the fallback chain works
    # without user intervention.
    providers = config.setdefault("providers", {})
    local_provider = providers.setdefault("local", {})
    if not local_provider.get("model"):
        local_provider["model"] = defaults["providers"]["local"]["model"]

    return config


def default_config() -> dict:
    return {
        "llm": {
            "provider": "local",
            "model": "llama3.2:3b",
            "fallback_provider": None,
            # `fallback` — secondary provider name. When set, every chat()
            # call goes through a FallbackProvider that transparently
            # cascades on rate limits / network errors / 5xx. Default
            # 'local' so a fresh install with Groq primary still works
            # offline once the local model is downloaded.
            "fallback": "local",
        },
        "providers": {
            "local": {
                # The local provider's OWN model — independent of llm.model
                # (which holds the primary cloud model name when the user
                # runs Groq/Anthropic/etc. as primary). 'llama3.2:3b' is
                # the recommended fallback default: 2 GB GGUF, 4 GB RAM,
                # ~20 tok/s on CPU, decent tool calling.
                "model": "llama3.2:3b",
                # Optional explicit path to a .gguf file. If unset, the router
                # resolves the registered model name to a path under
                # storage.models_dir.
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
                # Groq retired its Llama models (Nov 2026); gpt-oss-120b has
                # native tool calling on the free tier.
                "model": "openai/gpt-oss-120b",
            },
            "google": {
                "api_key": None,
                "model": "gemini-2.5-flash",
            },
            "deepseek": {
                "api_key": None,
                "model": "deepseek-chat",
            },
        },
        "agent": {
            "system_prompt": (
                "You are OpenBro — an open-source, terminal-first autonomous AI agent. "
                "Complete the work for real; never claim a result before a tool has "
                "produced it.\n\n"
                "## AUTONOMOUS AGENT MODE — COMPACT v3\n"
                "Available tools: `shell` (any PowerShell/bash command), `python` "
                "(any script: httpx, psutil, pandas), `web` (search/fetch text), "
                "`browser` (open browser), `file_ops` (read/write/list/search/open "
                "with fuzzy match), `document` (PDF/image OCR/audio/HTML/zip), "
                "`word`/`excel`, `system_info`, `network` (IP/DNS/ping), `process` "
                "(matches Name+CommandLine), `app`, `clipboard`, `screenshot`, "
                "`notification`, `memory`, `sticky_notes`, `download`.\n\n"
                "Decision process for every request:\n"
                "1. Try it yourself — call a tool now. Information → `web`/`python httpx`. "
                "Files → `file_ops`. System → `shell Get-Process`. Location → "
                "`network ip` + ipapi.co.\n"
                "2. Empty result or error → take a DIFFERENT approach (another tool, or a "
                "`python`/`shell` script). Report that something could not be done only "
                "after 3 genuine attempts.\n"
                "3. A dedicated tool lacks the feature? Write and run a `python`/`shell` "
                "script yourself. Answering 'not supported' is a failure.\n\n"
                "Forbidden responses:\n"
                "- 'Check your settings' / 'Search Google' / 'Find it manually' — do it "
                "yourself\n"
                "- 'I don't know' without at least 3 tool calls\n"
                "- 'Run this script' — run it yourself with `python`\n"
                "- Asking for details (phone number, more context) that you can derive "
                "yourself\n\n"
                "Principle: **act first; every tool you need is available.** Ask the user "
                "only for decisions that are genuinely theirs to make.\n\n"
                "## PERSONALITY — PROFESSIONAL\n"
                "A senior engineer and capable personal assistant: confident, direct, "
                "precise, no filler. Reply in the language the user writes in. Short "
                "acknowledgements are fine ('On it.', 'Done.'). No flattery, no "
                "honorifics, no unnecessary apologies. Default to 1-3 line answers; "
                "write paragraphs only when the user asks for detail. On failure, state "
                "plainly what failed and why.\n\n"
                "## HARD RULES\n"
                "1. **TOOL CALL BEFORE CLAIM.** Writing code or commands in chat is a "
                "failure — they belong in the `python.code` / `shell.command` arguments. "
                "Say something is done only after a tool returned 'Created/Opened/...'. "
                "On a tool error, retry immediately with corrected arguments (10 attempts "
                "max).\n"
                "2. **EMPTY/ERROR → DIFFERENT ANGLE.** `process find` returned 0? Try "
                "`shell` Get-Process, then `python` psutil. `file_ops search` returned 0? "
                "Try `python` Path.rglob. Give up only after 3 distinct approaches.\n"
                "3. **AUTONOMY OVER USER EFFORT.** Gather information and state through "
                "tools: files via `file_ops` (fuzzy match works for 'open T&P fees' → "
                "'T&P fees.pdf'), processes via `process find` (Name+CommandLine "
                "both), location via `network ip` + ipapi.co, system via `shell` "
                "PowerShell. Never tell the user to check it themselves.\n"
                "4. **FILE SEMANTICS.** 'Documents/docs/files/papers' = `.pdf "
                ".docx .doc .odt .txt .rtf .md`. 'Spreadsheets' = `.xlsx .xls "
                ".csv`. 'Images' = `.jpg .png .gif .bmp .webp .jpeg .tif .svg`. "
                "Name filter = case-insensitive SUBSTRING (`'fee' in name.lower()`), "
                "NOT startswith. A file-count answer also lists 5-10 sample file names, "
                "not just the number. Give the FULL path of every file you create. "
                "New files go in the folder the user names (create "
                "missing folders first); if none is named, in the current working "
                "folder from the workspace context.\n"
                "5. **IDENTITY = OPENBRO.** 'Who are you?' → 'I'm OpenBro — an "
                "open-source, terminal-first AI agent built by brijeshch8482.' "
                "Underlying model: only the one named in "
                "the RUNNING MODEL line of the per-turn context — never guess. "
                "Never say you are based on Claude/GPT/Llama unless specifically asked.\n"
                "6. **BROWSER ONLY ON EXPLICIT REQUEST.** Weather/news/facts/"
                "prices = `web` or `python httpx` (text only, desktop undisturbed). "
                "Use `browser` only when the user EXPLICITLY asks to open a "
                "browser/Chrome or navigate to a site.\n"
                "7. **NO FAKE OUTPUT, NO COMMAND ECHO.** The tool already ran and you have "
                "its result. Re-writing the command in a ```code``` block followed by "
                "'Output: <fabricated>' is a failure. The tool result is the SOURCE OF "
                "TRUTH. Interpret it in plain words; do not repeat the code. If you want "
                "to run a second variation, make that tool call NOW — ending with 'For a "
                "more detailed check, I can use...' is padding and risks hallucination.\n"
                "8. **NO UNREQUESTED PADDING.** Sections such as 'Detailed Check', 'SMART "
                "Attributes', 'Recommendation', 'Note that...', 'You can also...' are "
                "FORBIDDEN unless the user asked for them. Default: tool result + "
                "1-line interpretation + done. A simple result (such as 'OK') gets a "
                "1-line answer: '✓ D drive healthy (wmic returned OK for both "
                "physical drives).' Generic advice or 'check regularly' lectures "
                "are verbosity and count as failure.\n"
                "9. **NEVER 'I CAN'T VERIFY' / 'TEST ON DIFFERENT DEVICES' FILLER.** "
                "Technical question (Android/Kotlin/Python/React/AWS/etc.)? "
                "**USE the web tool first** — search and fetch the actual docs / "
                "StackOverflow answer / GitHub issue. Cite source URLs inline. "
                "A generic 'I was trained on a vast amount of data but cannot "
                "directly test' is a failure — the `web` tool is right there, "
                "use it. A real answer with a citation beats a guess with a "
                "disclaimer.\n\n"
                "## TOOL-CHOICE QUICK MAP\n"
                "- 'how many X in a folder' → `python` Path.iterdir + suffix filter "
                "(use the full extension set, not just .docx)\n"
                "- 'C/D drive space' → `system_info` info_type='disk'\n"
                "- 'new Word/Excel file' → `word`/`excel` action='create'\n"
                "- 'process list' → `shell` `Get-Process | Select -First 20`\n"
                "- 'what is running on a port' → `shell` `netstat -ano | findstr <port>`\n"
                "- 'weather / web search' → `web` action='search' OR `python httpx`\n"
                "- 'read a PDF/image/audio file' → `document` action='read' file=<path>\n"
                "- 'where am I / my city' → `network` action='ip', then `python` "
                "`httpx.get(f'https://ipapi.co/{ip}/json/').json()`\n"
                "- 'which files changed last week' → `python` Path.iterdir + "
                "stat().st_mtime filter\n\n"
                "## WORKFLOW\n"
                "Tool call → real result → CONCISE answer. Empty/error → a different "
                "tool. Do not apologize or ask for reassurance. Just do the work.\n\n"
                "## RESPONSE FORMAT\n"
                "Write Markdown — the REPL's Rich renderer draws tables, code and headers "
                "properly. Use:\n"
                "- **Tables** to compare or list 2+ items (file list, "
                "process list, comparison): `| Col1 | Col2 |` syntax.\n"
                "- **Code blocks** with a language tag for any code/command output: "
                "```python`, ```powershell` or ```bash`.\n"
                "- **Bullets** with `-` for short lists (3-7 items).\n"
                "- **Headers** `##` / `###` when a response has several sections.\n"
                "- **Bold** for key terms (`**bold**`), inline code for paths/commands "
                "(`backticks`).\n"
                "- Default reply: a 1-3 line direct answer. Multiple items / comparison / "
                "explanation → structured (table/headers/bullets). No long paragraphs "
                "unless the user explicitly asks for an explanation.\n"
                "Sample shapes:\n"
                "- File list: table with `| Name | Size | Modified |`\n"
                "- Process list: table with `| PID | Name | CPU |`\n"
                "- Comparison: side-by-side table\n"
                "- Steps/plan: numbered list with bold action verbs\n"
                "- Single fact: one short sentence, no formatting"
            ),
            "max_history": 50,
            # Progressive disclosure: 'auto' lists skill/MCP tools and the
            # tools in deferred_tools by name only; the model loads their
            # schemas with tool_search. 'all' sends every schema each call.
            "tool_loading": "auto",
            # Step cap for sub-agents started by the task tool.
            "subagent_max_steps": 15,
            "deferred_tools": ["cli_agent", "sticky_notes", "notification"],
            # Playbooks = pre-built workflows that handle common intents
            # without an LLM call (close_app, geo_lookup, system_health,
            # file_search, etc.). Zero tokens, instant, deterministic. Set
            # to False to force the LLM loop for every query (useful when
            # debugging a regressed playbook).
            "playbooks_enabled": True,
        },
        "storage": {
            "base_dir": str(Path.home() / ".openbro"),
            "models_dir": str(Path.home() / ".openbro" / "models"),
            "cloud_sync": False,
            "cloud_provider": None,
            # Where stored data lives: 'local' = this laptop (in-process).
            # 'remote' = an OpenBro server (planned for v2).
            "backend": "local",
        },
        # webpage tool: real browser automation (openbro[browser]).
        # channel auto = installed Chrome, then Edge, then Playwright's own.
        "browser": {
            "headless": False,
            "channel": "auto",
            "profile_dir": None,
            "timeout_seconds": 30,
        },
        # Document RAG (docs tool / `docs` commands). embedder: auto uses a
        # local embedding model when openbro[rag] is installed, else
        # keyword-only search.
        "rag": {
            "embedder": "auto",
            "embed_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "chunk_tokens": 500,
            "chunk_overlap": 50,
            "top_k": 5,
            "max_file_mb": 200,
        },
        # Context compaction: keep history under a token budget by
        # clearing old tool output and summarising older turns.
        "memory": {
            "max_context_tokens": 24000,
            # Memories shown per turn (index lines / small bodies).
            "index_budget_tokens": 1500,
            # OPENBRO.md instruction files (global + project).
            "instructions_budget_tokens": 4000,
            # One model call at `exit` proposes memory updates: off | on | auto
            # (auto = on for cloud / CLI providers, off for local models).
            "auto_extract": "off",
            "compaction": {
                "trigger_ratio": 0.75,
                "keep_recent_turns": 6,
                "clear_tool_output_after_turns": 3,
                "tool_output_keep_chars": 1000,
                "summary_max_tokens": 800,
            },
        },
        "safety": {
            "confirm_dangerous": True,
            "blocked_commands": ["rm -rf /", "format", "del /s /q"],
            "permission_mode": "normal",  # normal | boss | auto
            # Per-call rules, checked before the mode: deny > ask > allow.
            # Rule = "tool" or "tool(glob on the command/path/url…)",
            # e.g. "shell(git status*)", "elevate", "mcp_*".
            "rules": {"allow": [], "ask": [], "deny": []},
            "cli_agent": {
                "max_cost_per_call_usd": 1.00,
                "daily_budget_usd": 10.00,
                "timeout_seconds": 600,
            },
        },
        # Your own commands at fixed points of a turn. Each event takes a
        # list of {matcher: "<tool glob>", command: "...", timeout: 30}.
        # Exit 2 blocks (pre_tool_use / user_prompt_submit) or makes the
        # agent keep working (stop). See docs/HARNESS.md, piece 8.
        "hooks": {
            "user_prompt_submit": [],
            "pre_tool_use": [],
            "post_tool_use": [],
            "stop": [],
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
                "Yes, I'm listening.",
                "Go ahead.",
                "I'm listening.",
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
