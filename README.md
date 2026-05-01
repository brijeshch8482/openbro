<div align="center">

# OpenBro

### **Tera Apna AI Bro** — Open-Source Personal AI Agent

*Terminal pe ek command. Voice se bolo. Phone se chalao. Sab kuch on your laptop.*

[![CI](https://github.com/brijeshch8482/openbro/actions/workflows/ci.yml/badge.svg)](https://github.com/brijeshch8482/openbro/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0--beta-orange)](CHANGELOG.md)
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

### One-Line Install (auto-installer)

The installer creates a virtualenv, installs OpenBro, sets up the `openbro` command, and runs the first-time wizard.

<details>
<summary><strong>Windows</strong> (PowerShell, run as admin)</summary>

```powershell
iwr -useb https://openbro.sh/install.ps1 | iex
```

If `openbro.sh` is not yet pointing at the repo, fall back to:
```powershell
iwr -useb https://github.com/brijeshch8482/openbro/raw/main/scripts/install.ps1 | iex
```
</details>

<details>
<summary><strong>Linux / macOS</strong> (bash)</summary>

```bash
curl -fsSL https://openbro.sh/install.sh | bash
```

Fallback:
```bash
curl -fsSL https://github.com/brijeshch8482/openbro/raw/main/scripts/install.sh | bash
```
</details>

### From source (for contributors)

```bash
git clone https://github.com/brijeshch8482/openbro.git
cd openbro
pip install -e ".[dev,all,voice]"
openbro
```

### First Run

On first launch, OpenBro walks you through setup:

1. **Choose LLM** - Ollama (free/offline), Groq (free cloud), Claude, or GPT
2. **Auto Model Setup** - Ollama install + model download with progress bar
3. **Choose Storage Drive** - Pick where data and models are stored (D:, E:, etc.)
4. **Safety & Personality** - Configure command safety and response style

```
$ openbro

Step 1: Choose your LLM provider
  1. Ollama (offline, free, local) <-- recommended
  2. Groq (cloud, free tier, ultra-fast)
  3. Anthropic (Claude API, paid)
  4. OpenAI (GPT API, paid)

Step 2: Choose storage location
  Available Drives:
  #  Drive  Free Space  Total    Used %
  1  C:     25.3 GB     237 GB   89.3%
  2  D:     450.1 GB    500 GB   10.0%

  1. Default (~/.openbro)
  2. Custom path (choose your own drive/folder)
  3. Cloud folder (Google Drive / OneDrive)
```

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
| Ollama | Local/Offline | Free | Auto-installed by OpenBro |
| Groq | Cloud | Free tier | API key from console.groq.com |
| Anthropic | Cloud | Paid | API key from console.anthropic.com |
| OpenAI | Cloud | Paid | API key from platform.openai.com |

Switch provider anytime:
```
You > model groq
Switched to provider: groq (groq/llama-3.3-70b-versatile)

You > model ollama
Switched to provider: ollama (ollama/qwen2.5-coder:7b)
```

## Offline Models

OpenBro auto-downloads models. Available options:

| Model | Size | RAM | Best For |
|-------|------|-----|----------|
| qwen2.5-coder:7b | 4.7 GB | 8 GB | Coding (recommended) |
| qwen2.5-coder:3b | 2.0 GB | 4 GB | Coding (lighter) |
| qwen2.5-coder:1.5b | 1.0 GB | 4 GB | Low-end PCs |
| llama3.2:3b | 2.0 GB | 4 GB | General purpose |
| mistral:7b | 4.1 GB | 8 GB | General purpose |
| gemma2:2b | 1.6 GB | 4 GB | Lightweight |

Download anytime:
```
You > pull
# Shows interactive picker with sizes and RAM requirements

You > pull llama3.2:3b
# Direct download
```

## Uninstall

**Windows:**
```powershell
.\scripts\uninstall.ps1
```

**Linux/macOS:**
```bash
bash scripts/uninstall.sh
```

Or manually: `pip uninstall openbro`

## Requirements

- Python 3.10+
- (Optional) Ollama - auto-installed during setup
- (Optional) API keys for cloud providers

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

Talk to your bro with your voice:

```bash
pip install openbro[voice]   # installs whisper, edge-tts, sounddevice
openbro --voice
```

- **STT**: faster-whisper (offline, runs locally)
- **TTS**: Microsoft Edge voices via edge-tts (free, natural Indian English voice)
- **Wake words**: "hey bro", "hi bro", "ok bro", "bro suno" (configurable)

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
