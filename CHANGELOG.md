# Changelog

All notable changes to OpenBro are tracked here.

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
