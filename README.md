<div align="center">

# OpenBro

### **OpenBro** — Open-Source Personal AI Agent

*Terminal pe ek command. Voice se bolo. Phone se chalao. Sab kuch on your laptop.*

[![CI](https://github.com/brijeshch8482/openbro/actions/workflows/ci.yml/badge.svg)](https://github.com/brijeshch8482/openbro/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0b1-orange)](CHANGELOG.md)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[**Quick Start**](#quick-start) · [**Voice Mode**](#voice-mode) · [**Skills**](#skills-plugin-system) · [**Roadmap**](#roadmap) · [**Contributing**](CONTRIBUTING.md)

</div>

---

## What is OpenBro?

OpenBro is a free, open-source personal AI agent that runs on your machine with a single terminal command. No cloud subscription, no vendor lock-in. Your laptop, your control, your AI bro.

## What Can OpenBro Do?

OpenBro is a **full personal assistant** - not just a chatbot. Give it commands and it actually does the work:

```
You > Chrome open kar aur YouTube search kar 'arijit singh songs'
Bro: Opening Chrome and searching YouTube...

You > screenshot le aur D:/Screenshots me save kar
Bro: Saved to D:/Screenshots/screenshot_20260428_140532.png

You > spotify open kar de
Bro: Opening Spotify...

You > ye file https://example.com/file.pdf D:/Downloads me download kar
Bro: Downloaded file.pdf (2.3 MB) to D:/Downloads

You > volume 30 kar de
Bro: Volume set to 30%

You > screen lock kar
Bro: [confirms first since dangerous] Screen locked
```

## Features

|   | Feature | What it gives you |
|---|---------|-------------------|
| 🧠 | **Multi-LLM** | Ollama (offline), Claude, GPT, Groq — switch with one command |
| 🛠️ | **16 Built-in Tools** | Apps, browser, files, downloads, system control, screenshots, memory & more |
| 🔌 | **Skills / Plugins** | GitHub · Gmail · Google Calendar · Notion · YouTube — drop your own in `~/.openbro/skills/` |
| 🤖 | **Claude Code Orchestration** | Tu bole *"Claude se bolo X kar"* → OpenBro spawns `claude` CLI with cost limits, live progress |
| 🎙️ | **Voice Mode** | Whisper STT + Edge-TTS + wake words. Boss-mode permissions over voice |
| 🌐 | **Language Auto-Match** | Hindi ↔ English ↔ Hinglish — replies in the language you used |
| 📱 | **Telegram Bot** | Per-user memory isolation, allow-list, dangerous tools auto-blocked |
| 🧩 | **3-Tier Memory** | Working (RAM) + Session (SQLite) + Long-term (facts) |
| 🛡️ | **Boss Mode + Risk Tiers** | Safe / Moderate / Dangerous. Voice or chat permission for every tool |
| 🪟 | **Live Activity Panel** | Watch the agent think, call tools, get permissions in real time |
| 🔐 | **Privacy First** | Everything local. Audit log of every action. No telemetry |
| 💾 | **Custom Storage** | Pick any drive / folder for data + models. Single-click migrate |
| 🔄 | **Single-Click Models** | `model add claude`, `model switch qwen` — old offline model auto-cleanup |
| 🖥️ | **Cross-Platform** | Windows, Linux, macOS — same commands everywhere |

## Quick Start

### Recommended — install via pip

```bash
pip install openbro                # core
pip install "openbro[all,voice]"   # everything (telegram, voice, all providers)
openbro                            # first-run wizard auto-launches
```

### One-Line Install (auto-installer) ⭐ recommended

Single command, zero-friction. Auto-installs Python (if missing), pip-installs OpenBro with all extras, and launches the LLM setup wizard. End-to-end in 5–10 minutes.

**Windows** (PowerShell)
```powershell
$sha=(iwr -useb 'https://api.github.com/repos/brijeshch8482/openbro/commits/main'|ConvertFrom-Json).sha; iwr -useb "https://raw.githubusercontent.com/brijeshch8482/openbro/$sha/scripts/install.ps1" | iex
```

**Linux / macOS** (bash)
```bash
sha=$(curl -fsSL https://api.github.com/repos/brijeshch8482/openbro/commits/main | python3 -c 'import sys,json; print(json.load(sys.stdin)["sha"])') && curl -fsSL "https://raw.githubusercontent.com/brijeshch8482/openbro/$sha/scripts/install.sh" | bash
```

> **Why fetch the commit SHA first?** GitHub's `/raw/main/...` URL goes through CDN edge caches (Fastly) AND many ISPs (especially in India) run transparent HTTP caches that ignore query-string cache-busters like `?cb=...`. By fetching the latest commit SHA from GitHub's API and pinning the install URL to that exact commit, we get a different URL every push — zero cache collisions, always-fresh script.

<details>
<summary><strong>Simpler one-liner</strong> (works if your network doesn't aggressively cache)</summary>

```powershell
# Windows
iwr -useb https://raw.githubusercontent.com/brijeshch8482/openbro/main/scripts/install.ps1 | iex
```

```bash
# Linux/macOS
curl -fsSL https://raw.githubusercontent.com/brijeshch8482/openbro/main/scripts/install.sh | bash
```

If you get an old/stale version, switch to the SHA-pinned form above.
</details>

The installer will:
1. Detect & auto-install Python 3.12 (winget on Windows · brew on macOS · apt/dnf on Linux)
2. `pip install openbro[all,voice]` — Telegram + voice + every LLM provider
3. Auto-run `openbro --setup` so you finish with a fully configured, ready-to-chat OpenBro

### Update

The fastest way is to re-run the one-liner installer (idempotent — detects existing install, force-reinstalls openbro itself while keeping cached deps):

**Windows** (PowerShell)
```powershell
$sha=(iwr -useb 'https://api.github.com/repos/brijeshch8482/openbro/commits/main'|ConvertFrom-Json).sha; iwr -useb "https://raw.githubusercontent.com/brijeshch8482/openbro/$sha/scripts/install.ps1" | iex
```

**Linux / macOS** (bash)
```bash
sha=$(curl -fsSL https://api.github.com/repos/brijeshch8482/openbro/commits/main | python3 -c 'import sys,json; print(json.load(sys.stdin)["sha"])') && curl -fsSL "https://raw.githubusercontent.com/brijeshch8482/openbro/$sha/scripts/install.sh" | bash
```

Or update via pip directly (`llama-cpp-python` wheels live on a separate index, so the `--extra-index-url` is required):

```powershell
pip install --upgrade --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu "openbro[all,voice] @ git+https://github.com/brijeshch8482/openbro.git@main"
```

> ⚠️ Avoid `--no-deps` — it skips voice / provider deps and leaves optional features unavailable.

### Reopen the chat

After closing the terminal, open any new shell and run:
```bash
openbro
```
Your config, memory, and chat history are preserved at `~/.openbro/`. Just type `openbro` and the session resumes.

### Uninstall

Same one-liner — removes pip package, config, memory, and optionally local GGUF models + Whisper cache (asks you each time).

**Windows**
```powershell
$sha=(iwr -useb 'https://api.github.com/repos/brijeshch8482/openbro/commits/main'|ConvertFrom-Json).sha; iwr -useb "https://raw.githubusercontent.com/brijeshch8482/openbro/$sha/scripts/uninstall.ps1" | iex
```

**Linux / macOS**
```bash
sha=$(curl -fsSL https://api.github.com/repos/brijeshch8482/openbro/commits/main | python3 -c 'import sys,json; print(json.load(sys.stdin)["sha"])') && curl -fsSL "https://raw.githubusercontent.com/brijeshch8482/openbro/$sha/scripts/uninstall.sh" | bash
```

> Add `-Force` (PowerShell) or `OPENBRO_FORCE=1` (bash) for non-interactive uninstall. `-KeepData` / `OPENBRO_KEEP_DATA=1` preserves config + memory.

### From source (for contributors)

```bash
git clone https://github.com/brijeshch8482/openbro.git
cd openbro
pip install -e ".[dev,all,voice]"
openbro
```

### First Run

On first launch, OpenBro walks you through a 7-step wizard:

1. **Storage location** - Pick which drive holds data + models (e.g. `D:\OpenBro`)
2. **LLM provider** - Six options: Groq / Google Gemini / OpenAI / Anthropic Claude / DeepSeek / Local (offline)
3. **Safety** - Confirm dangerous commands? (default: yes)
4. **Personality** - Hinglish Bro / English Professional / Hindi
5. **Voice** - Always-on mic + wake word ("Hey bro") + TTS reply
6. **Telegram** - Optional phone bot (skip if not needed)
7. **MCP servers** - 5 servers auto-installed silently (filesystem, github, sqlite, time, fetch). Online ones stay dormant when offline.

```
$ openbro

Step 2: Choose your LLM
┌─────┬──────────────────────┬────────────┬──────────────────────────┐
│  1  │ Groq <-- recommended │    FREE    │ llama-3.3-70b-versatile  │
│  2  │ Google Gemini        │    FREE    │ gemini-1.5-flash         │
│  3  │ OpenAI               │ FREE-TRIAL │ gpt-4o-mini              │
│  4  │ Anthropic Claude     │    PAID    │ claude-sonnet-4-20250514 │
│  5  │ DeepSeek             │   CHEAP    │ deepseek-chat            │
│  6  │ Local (offline)      │  OFFLINE   │ llama3.2:3b              │
└─────┴──────────────────────┴────────────┴──────────────────────────┘
```

Pick **Local (offline)** and the wizard installs `llama-cpp-python` (~150 MB wheel), downloads `Llama 3.2 3B` (2 GB) from HuggingFace, and you're ready — fully offline forever after that one-time download.

## CLI Commands

| Command | Description |
|---------|-------------|
| `help` | Show all commands |
| `config` / `config set <k> <v>` | View / update configuration |
| `tools` | List available tools (with risk levels) |
| `storage` / `storage move` | View / migrate data + model storage |
| `audit` | Show recent tool execution log |
| **Memory** | |
| `memory` | Show stored facts and memory stats |
| `remember <key> <val>` | Save a fact (e.g. `remember name Brijesh`) |
| `forget <key>` | Delete a fact |
| `sessions` | List past conversation sessions |
| **Skills** | |
| `skills` | List installed skills with config status |
| **Models (single-click)** | |
| `model list` | List all models (offline + cloud) with status |
| `model add <name>` | Download Ollama model OR store API key |
| `model switch <name>` | Switch active model — offers to remove old offline |
| `model remove <name>` | Uninstall offline OR clear cloud API key |
| **Activity environment** | |
| `show` | Open live activity panel (agent's environment) |
| `hide` | Close panel — agent keeps running in background |
| `activity` | Print last 30 events one-shot |
| **Permissions** | |
| `boss` / `boss off` | Toggle Boss mode — ask before EVERY tool |
| **Other** | |
| `clear` / `reset` / `exit` | Clear screen / clear history / quit |

## CLI Flags

```bash
openbro                    # Normal start
openbro --setup            # Re-run setup wizard
openbro --provider groq    # Start with specific provider
openbro --model gpt-4o     # Start with specific model
openbro --offline          # Force offline mode (Ollama)
openbro --telegram         # Run as Telegram bot
openbro --voice            # Run in voice mode (mic + TTS)
openbro --version          # Show version
```

## Storage Management

OpenBro lets you choose where everything is stored:

- **Data** (memory, history, cache, logs) - any drive/folder you pick
- **Models** (Ollama models, 1-5 GB each) - can be on a separate drive
- **Cloud Sync** (optional) - Google Drive, OneDrive, Dropbox folder

```
You > storage

Storage Info:
  Item     Path                        Size
  base     D:\OpenBro-Data             2.3 MB
  memory   D:\OpenBro-Data\memory      156 KB
  models   D:\OpenBro-Data\models      4.7 GB
  cache    D:\OpenBro-Data\cache       512 KB

You > storage move
  Enter new path: E:\MyAI
  Move all data? [y/N]: y
  Data moved to: E:\MyAI
```

## Built-in Tools (15 total)

OpenBro is a full personal assistant - tools are categorized by risk level:

### Safe Tools (read-only, no confirmation needed)
| Tool | What it does |
|------|-------------|
| `system_info` | OS, disk, and environment info |
| `web` | Web search (DuckDuckGo) and URL fetching |
| `network` | Ping, public/local IP, DNS lookup, connectivity |
| `datetime` | Current time, date math, timezones |
| `clipboard` | Read from / write to clipboard |
| `screenshot` | Capture screen to file |
| `notification` | Show desktop notifications |
| `memory` | Remember/recall/search persistent facts |

### Moderate Tools (modify files / open apps)
| Tool | What it does |
|------|-------------|
| `file_ops` | Read, write, list, and search files |
| `app` | Open/close any installed application |
| `browser` | Open URLs, search Google/YouTube/GitHub/etc. |
| `download` | Download files from URLs to chosen folders |
| `process` | List, find, kill processes |

### Dangerous Tools (system-level - asks confirmation)
| Tool | What it does |
|------|-------------|
| `shell` | Execute shell commands |
| `system_control` | Lock/sleep/shutdown/restart, volume, mute |

## LLM Providers

| Provider | Type | Cost | Setup |
|----------|------|------|-------|
| **Local (offline)** | In-process llama.cpp | Free, forever | Auto: pulls GGUF from HuggingFace |
| Groq | Cloud | Free tier (30 req/min) | API key from console.groq.com |
| Google Gemini | Cloud | Free tier (1500 req/day) | Key from aistudio.google.com |
| OpenAI | Cloud | $5 trial credit | Key from platform.openai.com |
| Anthropic Claude | Cloud | Paid | Key from console.anthropic.com |
| DeepSeek | Cloud | Cheap ($0.14/M tok) | Key from platform.deepseek.com |

The local backend is **llama-cpp-python** — same `llama.cpp` engine that Ollama / LM Studio wrap under the hood, but in-process (no daemon, no HTTP hop, ~10–20 % faster). GGUF models stream directly from HuggingFace; nothing else runs in the background.

Switch provider any time:
```
You > model switch groq
✓ Switched to: groq / llama-3.3-70b-versatile

You > model switch llama          # alias for local + llama3.2:3b
✓ Switched to: local / llama3.2:3b
```

## Offline Models

10 curated GGUF models, non-Chinese vendors (Meta / Mistral / Microsoft / Google) — all Q4_K_M quantization for the best size-vs-quality trade-off on CPU.

| Model | Size | RAM | Speed (CPU) | Best For |
|-------|------|-----|-------------|----------|
| **llama3.2:3b** | 2.0 GB | 4 GB | ~6 s reply | Recommended default — fast + capable |
| llama3.2:1b | 0.8 GB | 2 GB | ~2 s reply | Low-end PCs |
| llama3.1:8b | 4.9 GB | 8 GB | ~30 s reply | Highest quality (needs GPU for speed) |
| llama3.3:70b | 40 GB | 48 GB | GPU only | Top-tier (workstation) |
| mistral:7b | 4.4 GB | 8 GB | ~10 s reply | Reliable all-rounder |
| mistral-nemo | 7.5 GB | 12 GB | ~15 s reply | 128K context |
| codestral:22b | 13 GB | 16 GB | ~25 s reply | Code specialist |
| phi3:mini | 2.4 GB | 4 GB | ~5 s reply | Microsoft tiny |
| phi3:medium | 8.6 GB | 16 GB | ~25 s reply | Microsoft 14B |
| gemma2:2b | 1.7 GB | 4 GB | ~3 s reply | Google lightweight |
| gemma2:9b | 5.8 GB | 8 GB | ~15 s reply | Google strong chat |

Manage models from the terminal:
```bash
openbro model list                       # show installed + catalogue
openbro model download llama3.2:3b       # fetch from HuggingFace (direct httpx stream, no XET stall)
openbro model import D:/x/model.gguf     # USB-transferred file (air-gapped PCs)
openbro model remove <name>              # free disk
```

In-chat:
```
You > pull                               # interactive picker
You > pull llama3.2:3b                   # direct
You > model switch llama-tiny            # llama3.2:1b alias
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| **First local reply is slow** | First chat on a fresh model = 30-90 s while llama.cpp mmaps the GGUF. If still stuck after 2 min, switch to `llama3.2:3b` (`openbro config set llm.model llama3.2:3b`) — 8B on CPU is too slow for daily use. |
| **Voice doesn't respond to "hey openbro"** | Windows: Settings → Privacy → Microphone → "Allow desktop apps" = ON. Speak close to the mic. Run `voice test` inside OpenBro to verify mic + STT. |
| **GitHub MCP server has no token** | `openbro mcp creds github` — interactive prompt, hidden input, saves to config. Restart `openbro`. |
| **Install failed with "egg fragment invalid"** | pip >= 23 needs PEP 508 syntax. Use: `openbro[all,voice] @ git+https://...` instead of `git+https://...#egg=openbro[...]`. The installer scripts use the correct form. |
| **`llama-cpp-python` source-compile crashed** | PyPI has no binary wheels for this package. Use `--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu` — the installer does this automatically. |

## Requirements

- Python 3.10–3.13 (3.12 recommended — installer auto-installs it on Windows if missing)
- (Optional) API keys for cloud providers — get free keys from Groq / Google AI Studio
- (Optional) Node.js for MCP servers — installer auto-installs via winget / brew / apt

## CLI Agent Orchestration 🤝

OpenBro can delegate complex coding tasks to other AI CLIs and stream their progress live. You stay in command — OpenBro is your boss; the CLI is your worker.

```
You > Hey bro, Claude se bolo openbro me ek weather tool add kar
Bro: Theek hai, Claude ko delegate karta hu...
     [permission: cli_agent (moderate) → allow]

╭─ 🤖 Claude Code · D:/OpenBro · cap $1.00 ──────╮
│ ✓ Read pyproject.toml                            │
│ ✓ Read openbro/tools/base.py                     │
│ ✓ Wrote weather_tool.py (54 lines)               │
│ ✓ Edited registry.py                             │
│ ✓ Wrote test_weather_tool.py                     │
│   ⏱ 47s · 💰 $0.11                              │
╰──────────────────────────────────────────────────╯

Bro: Bhai, weather tool ban gaya. 3 files changed, $0.11 cost.
```

**Supported CLIs** (auto-detected — install whichever you use):

| CLI | Best for | Install |
|-----|----------|---------|
| **Claude Code** (`claude`) | Multi-file refactors, careful work | `npm i -g @anthropic-ai/claude-code` |
| **Codex** (`codex`) | Fast single-file edits | `npm i -g @openai/codex` |
| **Aider** (`aider`) | Want every change as a git commit | `pip install aider-chat` |
| **Gemini** (`gemini`) | Big-context tasks, summaries | `npm i -g @google/gemini-cli` |

**Cost guard** — every call has a per-call USD cap and a daily budget (defaults: $1/call, $10/day, configurable in `safety.cli_agent`). Spend tracked per agent in `~/.openbro/cli_agent_spend.json`.

**Adding a new CLI**: drop a `CliAgent` subclass in `openbro/orchestration/`, register it in `registry.py`. ~50 lines.

## Live Activity Environment 🪟

Every action the agent takes — thinking, tool calls, permission asks, sub-agent invocations — is published to a live event bus.

```
You > show
🤖 Activity panel started.

╭──── 🤖 OpenBro Activity ────╮
│ 14:22:01  user         Chrome khol de              │
│ 14:22:01  thinking     agent thinking…             │
│ 14:22:02  tool_start   app (moderate)              │
│ 14:22:02  permission   asking for app (moderate)   │
│ 14:22:04  permission   app: ALLOWED                │
│ 14:22:04  tool_end     app done                    │
│ 14:22:05  assistant    Chrome khol diya bhai!      │
╰─────────────────────────────╯

You > hide   # close panel; agent keeps running silently
```

A background log is **always written** to `~/.openbro/logs/activity.log` whether the panel is open or not — useful for debugging or audit later.

## Boss Mode 🛡️

By default OpenBro asks permission only for **dangerous** tools. Switch to Boss mode and it asks for **every** tool — you stay in control of every action.

```
You > boss
Boss mode ON.

You > Chrome khol de
[Permission required]
  Tool: app
  Risk: moderate
  Args: {'action': 'open', 'app_name': 'chrome'}
  > [y]es / [n]o / [a]lways allow / [d]eny always
```

**Voice mode auto-enables Boss mode** and asks via TTS:

> 🔊 *"Bhai, app tool chalana hai. Risk moderate hai. Permission du? Haan ya nahi?"*

Reply with *"haan"* / *"yes"* / *"kar de"* (allow) or *"nahi"* / *"no"* / *"mat kar"* (deny). Hindi + English + Hinglish all parsed. Negation always wins for safety.

## Language Auto-Match 🌐

OpenBro replies in whatever language you used:

| You write/speak | Reply | Voice (in `--voice`) |
|-----------------|-------|----------------------|
| `क्रोम खोल दे` | Pure Hindi (Devanagari) | `hi-IN-SwaraNeural` |
| `chrome khol de bro` | Casual Hinglish | `en-IN-NeerjaNeural` |
| `open chrome please` | Pure English | `en-IN-NeerjaNeural` |

Detection runs every message — switch languages mid-conversation, OpenBro follows.

## Memory System (3-tier)

OpenBro remembers things across sessions:

- **Working memory** - the last N messages of the current chat (in RAM)
- **Session memory** - full conversation history (SQLite, per session_id)
- **Long-term memory** - persistent facts you teach it (`remember name Brijesh`)

```
You > remember favorite_food biryani
Remembered: favorite_food = biryani

You > memory
Memory stats: 1 facts, 24 messages, 3 sessions

You > sessions
# lists past conversation sessions with timestamps
```

The `memory` tool is also exposed to the LLM, so the model can store/recall facts mid-conversation.

## Telegram Bot

Chat with your bro from your phone:

```bash
# 1. Get a bot token from @BotFather on Telegram
openbro --setup    # walk through Telegram step
# OR
openbro config set channels.telegram.token <BOT_TOKEN>
openbro config set channels.telegram.allowed_users [123456789]

# 2. Run as bot
openbro --telegram
```

Each Telegram user gets their own isolated memory + session. Dangerous tools are blocked by default in non-interactive mode.

## Skills (Plugin System)

5 built-in skills + your own:

| Skill | Tools | Config Required |
|-------|-------|-----------------|
| `github` | search repos, repo info, issues | none (token optional for write) |
| `youtube` | search videos, transcripts | none |
| `gmail` | inbox, send mail | email + app password |
| `calendar` | upcoming Google Calendar events | private iCal URL |
| `notion` | search/read/create pages | integration token |

Configure via:
```
You > config set skills.github.token ghp_xxx
You > config set skills.notion.token secret_xxx
You > skills
```

Drop custom skills in `~/.openbro/skills/<name>/skill.py` - they auto-load.

## Voice Mode

Talk to OpenBro with your voice:

```bash
pip install openbro[voice]   # installs whisper, edge-tts, sounddevice
openbro --voice
```

- **STT**: faster-whisper `small` by default (offline), with opt-in Groq cloud STT via `voice cloud on`
- **TTS**: Microsoft Edge voices via edge-tts (free, natural Indian English voice)
- **Wake words**: "hey openbro", "hi openbro", "ok openbro", "openbro suno" (configurable)

## Project Status

Currently in **v0.5** - Voice layer + closed beta. See roadmap below.

### Roadmap

- [x] **v0.1** - Foundation: CLI, multi-LLM, basic tools, install scripts
- [x] **v0.2** - Personal assistant tools, risk classification, audit logging
- [x] **v0.3** - Telegram bot + 3-tier memory system
- [x] **v0.4** - Skills/plugin system + 5 launch skills
- [x] **v0.5** - Voice layer (Whisper STT + Edge-TTS + wake words)
- [ ] **v0.8** - Public beta, docs site, BroHub marketplace
- [ ] **v1.0** - Stable launch

## Project Structure

```
openbro/
├── openbro/
│   ├── core/           # Agent brain
│   ├── llm/            # LLM providers (Ollama, Claude, GPT, Groq)
│   ├── tools/          # 15 built-in tools (file, shell, system, web, memory, ...)
│   ├── channels/       # Input/output: CLI, Telegram (+ future channels)
│   ├── memory/         # 3-tier memory: working + session + long-term
│   ├── voice/          # STT (whisper), TTS (edge-tts), wake-word listener
│   ├── skills/         # Plugin system + 5 built-in skills
│   ├── cli/            # Terminal REPL, wizard, voice mode
│   └── utils/          # Config, storage, Ollama setup, audit
├── scripts/            # Install/uninstall scripts
├── tests/              # 114 tests
├── pyproject.toml
├── LICENSE (MIT)
└── README.md
```

## License

[MIT](LICENSE) - Free forever. Core will always be open source.

## Author

Built by [Brijesh Chaudhary](https://github.com/brijeshch8482)
