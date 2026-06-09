# Changelog

All notable changes to OpenBro are tracked here.

## [Unreleased] — Custom model training pipeline

**Headline: `openbro train` builds your own `openbro.gguf` from public web data.**

A complete maintainer pipeline + end-user distribution layer for shipping a custom-fine-tuned LLM with OpenBro:

- **`openbro train`** (maintainer) — full automated pipeline: fetches public training data (Stack Overflow / GitHub Issues / Wikipedia / Reddit / ArXiv), LoRA-fine-tunes `Llama-3.2-1B-Instruct` on local GPU (~4–6 hours on RTX 3050 4 GB VRAM), merges + quantizes to a 700 MB Q4_K_M GGUF, runs smoke tests, replaces the live model, auto-creates a GitHub PR, and uploads to HuggingFace Hub.
- **`openbro train setup`** — pre-flight checks: ML libraries, CUDA, HuggingFace login, gh CLI, llama.cpp checkout, model-repo clone, base-model license access. Prints a green/red checklist with fix hints for each failure.
- **`openbro train status`** — past training-run history with dataset size, loss, GGUF size, PR URL, HuggingFace upload status, and days-since-last-run.
- **`openbro train run`** — explicit form of the bare `openbro train`; supports `--no-publish`, `--skip-fetch`, `--quick` (1 epoch debug), `--base-model`, `--root`.
- **`openbro model update`** (end user) — pulls the latest `openbro.gguf` from `brijeshch8482/openbro-1b-instruct` on HuggingFace via the free CDN, sha-checks against the local copy, backs up + replaces if newer.
- **`openbro model info`** — shows the configured local model + every GGUF on disk with sizes.
- **`openbro:1b`** registered in the model catalogue alongside the existing Llama / Mistral entries. `openbro model download openbro:1b` works the same as any other model.
- **Auto-prefer**: when `openbro.gguf` is on disk, the local provider uses it automatically without requiring a config change.
- **New module**: `openbro/training/` with `data_sources.py`, `dataset.py`, `finetune.py`, `to_gguf.py`, `validate.py`, `publish.py`, `setup_helper.py`, `status.py`, `cli.py`.
- **New optional extras**: `pip install 'openbro[training]'` pulls torch / transformers / peft / bitsandbytes / datasets / accelerate / huggingface_hub / llama-cpp-python. Core deps stay light.
- **Privacy by design**: training uses public APIs only — no end-user data collection. Architecture details in [docs/TRAINING.md](docs/TRAINING.md).
- **22 new unit tests** in `tests/test_training_pipeline.py` covering text cleaning, JSONL building, dedup, length filtering, status loading, and CLI command registration.

## [1.0.0-beta] - Stable beta

**Headline: CLI agent orchestration — OpenBro can drive other AI CLIs.**

- `openbro/orchestration/` package with 4 adapters (`Claude`, `Codex`, `Aider`, `Gemini`). Each adapter knows its CLI's command syntax + output format and emits live progress events to the activity bus.
- New unified `cli_agent` tool replaces v0.6's `claude_code` tool. Single tool, `agent` parameter picks adapter. LLM auto-detects which is installed.
- Per-agent daily budget tracker in `~/.openbro/cli_agent_spend.json`. Per-call cap + daily cap (default $1 / $10) configurable in `safety.cli_agent`.
- 19 new tests covering all four adapters + spend tracking + budget gates.

**Activity environment**

- `core/activity.py`: thread-safe pub-sub event bus. Every agent action emits an event.
- `cli/activity_panel.py`: Rich Live foreground panel (`show` / `hide` REPL commands) + always-on background log at `~/.openbro/logs/activity.log`.
- Agent emits `user`, `thinking`, `tool_start`, `tool_end`, `permission`, `assistant`, `cli_agent` events.

**Boss-mode permissions**

- `core/permissions.py`: `PermissionGate` with 3 modes (`normal` / `boss` / `auto`) and 3 channels (`cli` / `voice` / `silent`).
- Voice channel: TTS asks "haan ya nahi?", parses yes/no across Hindi + English + Hinglish (negation always wins).
- Per-tool "always allow / deny always" session memo. REPL: `boss` / `boss off`. Voice mode auto-enables Boss mode.

**Language auto-match**

- `utils/language.py`: `detect_language()` returns `hi` / `hinglish` / `en`. Devanagari → pure Hindi, ≥15% Hinglish keywords → casual Hinglish, else pure English.
- Agent injects per-message language instruction into the system prompt.
- Voice mode auto-switches TTS voice (`hi-IN-Swara` ↔ `en-IN-Neerja`).

**Single-click model manager**

- `cli/model_manager.py`: `model add`, `model switch`, `model remove`, `model list` for both offline (Ollama) and cloud (Anthropic/OpenAI/Groq) models. Aliases: `claude`, `gpt`, `groq`, `qwen`, `llama`, `mistral`, `gemma`. `model switch` offers to delete the old offline model to free disk.

**Zero-friction installer**

- `scripts/install.ps1` (Windows) and `scripts/install.sh` (Linux/macOS) rewritten: 5 colored steps with box-drawn header, Python + Ollama auto-detect, optional Ollama silent install, PATH check, optional auto-launch. Falls back PyPI → GitHub. Default extras = `all,voice` so you get Telegram + voice + every provider in one shot.

**Polish**

- README rewritten with badges, feature matrix, CLI orchestration examples, live activity demo, Boss-mode walkthrough.
- 169 tests (was 102 in v0.4). All 8 CI jobs green (Ubuntu + Windows × Python 3.10–3.13).
- `pyproject.toml` version → `1.0.0b1`.

## [0.5.0] - Voice layer

- `openbro/voice/stt.py` — `SpeechToText` wrapping faster-whisper (offline, lazy-loaded).
- `openbro/voice/tts.py` — `TextToSpeech` using edge-tts (free, natural Indian voice) with pyttsx3 offline fallback. Cross-platform audio playback.
- `openbro/voice/listener.py` — `VoiceListener` with mic capture, energy-based VAD, substring wake-word detection.
- `openbro --voice` flag → starts mic-in / TTS-out loop with wake words `hey bro`, `ok bro`, etc.
- New extra: `pip install openbro[voice]`.
- 12 new tests (mocked, no real audio I/O in CI).

## [0.4.0] - Skills / plugin system

- `BaseSkill` interface + `SkillRegistry` that auto-loads built-in and user skills (`~/.openbro/skills/<name>/skill.py`).
- 5 launch skills: `github`, `youtube`, `gmail` (IMAP/SMTP via app password), `calendar` (private iCal URL), `notion`.
- `ToolRegistry` now accepts a config dict and registers configured skill tools alongside built-ins.
- `skills` REPL command + help entry.
- 22 new tests (`test_skills.py`).

## [0.3.0] - Telegram bot + 3-tier memory

- SQLite-backed persistent memory: facts, conversations, sessions tables.
- `MemoryManager` with working / session / long-term tiers. Session IDs survive restarts.
- `MemoryTool` exposed to the LLM (remember, recall, forget, search, list).
- Telegram bot channel (`openbro --telegram`) with per-user memory isolation and authorisation whitelist. Dangerous tools blocked in non-interactive mode.
- REPL: `memory`, `remember`, `forget`, `sessions` commands.
- Wizard step 5 for Telegram setup.
- 24 new tests across memory and Telegram.

## [0.2.0] - Personal assistant tools

- 9 new tools: `app`, `browser`, `clipboard`, `download`, `notification`, `process`, `screenshot`, `system_control`, `network`, `datetime`.
- Risk classification (`SAFE` / `MODERATE` / `DANGEROUS`) with per-call confirmation prompts for dangerous actions.
- Audit logging in JSONL (`~/.openbro/audit.log`) for every tool execution.
- REPL `audit` command to view recent executions.

## [0.1.0] - Foundation

- Click CLI + prompt-toolkit REPL + Rich UI.
- LLM provider abstraction (Ollama, Anthropic, OpenAI, Groq) with router.
- 5 base tools (file, shell, system_info, web, …) with JSON-schema interface.
- Cross-platform install/uninstall scripts (PowerShell + bash).
- Auto Ollama install + model download with RAM-aware picker.
- Custom storage path support (drive selection, migration).
- YAML config in `~/.openbro/config.yaml`. First-run wizard.
- GitHub Actions CI matrix (Ubuntu + Windows × Python 3.10–3.13).
