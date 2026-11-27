# Changelog

All notable changes to OpenBro are tracked here.

## [Unreleased]

- **Codex works as the model only.** OpenBro ran `codex exec` as a full agent with its own shell, image generator, web search and file tools. When Codex was the backup, it made images into its hidden `~/.codex/generated_images` folder: OpenBro never saw a tool step, never asked permission, and couldn't say where the file was. Codex now runs read-only, with its built-in tools and web search switched off (feature names this Codex version doesn't know are dropped automatically). It is told to act through OpenBro's `<tool_call>` tags.
- **A call to a tool that doesn't exist is answered.** When a text-protocol model calls a tool OpenBro doesn't have, the model is told so once and answers again, instead of the raw tag reaching the user.
- **Token counts in words.** `0↓ 0↑` is replaced by "9.3K tokens sent · 250 received", and it is hidden when the model doesn't report counts (the Claude and Codex apps). Codex's count is read again: newer CLIs print it on stderr.
- **Backup notice says why.** For example: "groq didn't answer (request too large for its free limit) — using backup codex for this turn."
- **No more tool loops — every provider sends structured tool history.** Gemini, OpenAI, DeepSeek and the local engine dropped the model's own tool calls from the history it was sent, so the model never saw that it had already acted and kept calling the same tool (9 identical image runs in one test); OpenAI and DeepSeek also rejected any 2-step tool task. A shared converter (`openbro/llm/wire.py`) now pairs every call and result by id (ids are made up for calls stored without one); Gemini gets real `functionCall` / `functionResponse` parts with thought signatures kept; local models see their calls as text. DeepSeek raises errors instead of returning them as the reply, so fallback works.
- **Behaviour:** when a request leaves open what only the user can decide ("make an image" — of what?), the agent asks one short question with suggestions; once the work succeeded it stops and replies with what it made, where, and how to change it. New files go to the folder the user names (missing folders are created) or the current working folder — no longer the Desktop by default (saved prompts migrated).
- **The agent names the right model:** each turn carries a RUNNING MODEL line read from the live provider, so "which model are you?" is answered correctly right after a `/model` switch. The old default prompt hard-coded "Groq pe Llama 4"; saved prompts have that one sentence replaced (other edits kept). Groq's name reports the model that actually answered after a failover, retired Groq models are skipped for the session, and saved configs naming a retired Groq model move to `gpt-oss-120b`.
- **One Ctrl+C ends the chat**, even while an answer is being worked on (the turn runs in a worker thread and Ctrl+C is checked every 0.1 s; before, Windows held the interrupt until the answer finished). On exit OpenBro prints `Resume this chat: openbro --resume <id>`; resumed chats keep saving to the same session. The duplicate "Voice is not active." lines on exit are gone.
- **`/model` picker:** an arrow-key list of every LLM (✓ = ready). Ready ones switch at once; an API provider without a key asks for it (hidden), tests it with a real one-line request, and saves and switches only if that works — a failing key is never saved. CLI or local providers that are not set up explain what to install. `/model groq` etc. use the same flow.
- **Google:** the API key now travels in a request header instead of the URL, so it can no longer appear in error messages or logs; errors shown by the picker hide anything key-like. The retired `gemini-1.5-flash` default is now `gemini-2.5-flash`.
- **`/model` shows and switches properly:** it now lists the main LLM, the backup, which one answered last, and every provider you can switch to (✓ when ready on this computer). `/model claude` and `/model codex` switch back to the subscription CLIs (they were not recognised before); a failed switch leaves everything unchanged; `/model <model-id>` changes the current provider's model. `model list/add/switch/remove` work again — the plain `model <name>` rule used to swallow them.
- **Free hosting on Render:** `render.yaml` Blueprint (free Docker web service). The image uses a small English embedding model (`OPENBRO_WEB_EMBED_MODEL`, default `BAAI/bge-small-en-v1.5`; `none` = keywords only), so the demo runs in ~245 MB — tested under 512 MB RAM and 0.1 CPU. The embedding model warms up in the background at start, so the first search takes ~1 s instead of ~17 s. Hugging Face Docker Spaces now need PRO; the guide covers both.
- **Deploy the web demo:** `Dockerfile` (any container host) and `deploy/huggingface/` (free Hugging Face Space that builds from GitHub), plus `docs/DEPLOY.md`. `python -m openbro.web.prepare` bakes the embedding model and demo index into the image; the container starts in about 2 seconds and runs as an unprivileged user.
- **Document search really uses meaning now:** the default embedding model was not available in fastembed (the `rag` extra), so search silently stayed keyword-only. The default is now `paraphrase-multilingual-MiniLM-L12-v2` (384 dims, ~220 MB, Hindi + 50 languages, works in fastembed and sentence-transformers); configs naming the old default switch automatically unless sentence-transformers + torch can load it.
- **`openbro web` — public demo chat page.** FastAPI server plus a dependency-free chat page (dark/light, mobile) that streams each step live: thinking, tool calls with timings, then the Markdown answer. Visitors get a locked-down agent: `web` (public addresses only, every redirect re-checked), `datetime` and `docs` over a separate demo index (search/read/list only); SAFE-risk calls only; no host memory, instruction files, workspace, hooks or playbooks; per-visitor event bus and in-memory history. Rate limits per IP and overall, turn and message caps, concurrency cap, capped tool output and a small history budget for free-tier token limits. New `web` extra.
- **Groq:** its Llama models were retired, so the default is now `openai/gpt-oss-120b` with `qwen/qwen3.8-27b` and `openai/gpt-oss-20b` as fallbacks. A saved config naming a retired model, a rejected tool call or a rate limit moves to the next model instead of failing.
- **Agent:** an empty final reply (some models end with reasoning only) gets one nudge to answer instead of showing nothing. Agents can take their own event bus.
- **`webpage` tool — operate web pages in a real browser.** Open, snapshot (numbered links / buttons / fields), click (by number or visible text), type, select, press, scroll, read, screenshot, tabs, wait. Drives the installed Chrome or Edge (Playwright's Chromium as a fallback) with a persistent OpenBro profile, from one worker thread. Buy / pay / send / delete clicks and password typing are dangerous-risk, so the user is asked first. New `browser` extra; `all` now also includes `playwright` and `fastembed`. `doctor` reports web page control.

## [1.1.0] — Agent harness v2

**Headline: the harness around the model is rebuilt — eight pieces, each shipped through its own pull request with the full CI matrix green.**

- **Tool protocol** — text-only models (subscription CLIs, local specialists) can now use every OpenBro tool through `<tool_call>` tags; the permission gate and audit log apply to them too. Stable, cache-friendly system prompt with a per-turn reminder.
- **Transcripts and compaction** — every message is saved, tool calls included, so `--resume` is lossless. Long sessions are summarised instead of truncated. New `context` and `compact` commands.
- **Memory v2** — one Markdown note per memory, ranked per turn; `OPENBRO.md` instruction files (global and per project); memory tool with save/read/delete/list/search; `memory open` / `memory edit`.
- **Search tools** — `grep`, `glob`, `read` (line ranges) and `edit` (exact replacement, read first).
- **Document search** — index folders in place with `docs add`; keyword + meaning search merged into one ranked list, page citations, incremental re-indexing, export/import. New `docs` tool and commands; `openbro[rag]` extra.
- **Lean requests** — plugin and rarely used tools are listed by name and loaded with `tool_search` when needed.
- **Sub-agents** — the `task` tool runs a helper with a fresh context and returns only its report.
- **Rules and hooks** — `safety.rules` allow/ask/deny and `hooks` scripts at prompt, tool and stop events.
- **Maintenance** — `openbro doctor`, `openbro update`, `openbro uninstall`.
- README rewritten; design docs in `docs/HARNESS.md`, `docs/RAG.md`, `docs/MEMORY.md`.
- Server-ready storage: transcripts, memories and the document index sit behind a local backend; `storage.backend: remote` is reserved for the upcoming OpenBro server.

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
