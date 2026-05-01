# OpenBro

**Tera Apna AI Bro** - Open-Source Personal AI Agent

> Terminal pe ek command, aur tera personal AI bhai ready hai.

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

- **Personal Assistant** - Open apps, download files, control system, automate everything
- **Multi-LLM Support** - Claude, GPT, Groq (free), Ollama (offline)
- **Offline-First** - Works without internet using local models
- **Auto Setup** - Ollama install, model download, everything automatic
- **15 Built-in Tools** - Apps, browser, files, downloads, system control, memory, and more
- **Risk Classification** - Safe/Moderate/Dangerous tiers with confirmation prompts
- **Audit Logging** - Every tool execution logged for transparency
- **3-Tier Memory** - Working (in-RAM) + Session (SQLite) + Long-term (facts)
- **Telegram Bot** - Chat with your bro from your phone, per-user isolation
- **Skills/Plugins** - GitHub, Gmail, Calendar, Notion, YouTube + bring your own
- **Voice Mode** - Whisper STT + Edge-TTS + wake words ("Hey bro")
- **Terminal CLI** - Rich interactive REPL with Hinglish support
- **Provider Agnostic** - Switch LLM with one config line
- **Custom Storage** - Choose your drive/folder for data and models
- **Cloud Backup** - Optional Google Drive/OneDrive/Dropbox sync
- **Streaming** - Real-time response output
- **Privacy by Default** - Everything runs locally, no data collection
- **Cross-Platform** - Windows, Linux, macOS

## Quick Start

### One-Line Install

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/brijeshch8482/openbro/main/scripts/install.ps1 | iex
```

**Linux / macOS:**
```bash
curl -fsSL https://raw.githubusercontent.com/brijeshch8482/openbro/main/scripts/install.sh | bash
```

### Or via pip

```bash
pip install openbro
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
| `config` | View current configuration |
| `config set <key> <val>` | Update config |
| `model` | Show current LLM model |
| `model <name>` | Switch model or provider |
| `models` | List downloaded offline models |
| `pull` | Download a new offline model |
| `pull <model>` | Download specific model |
| `tools` | List available tools (with risk levels) |
| `storage` | View storage usage and paths |
| `storage move` | Move data to another drive |
| `audit` | Show recent tool execution log |
| `memory` | Show stored facts and memory stats |
| `remember <key> <val>` | Save a fact (e.g. `remember name Brijesh`) |
| `forget <key>` | Delete a fact |
| `sessions` | List past conversation sessions |
| `skills` | List installed skills with config status |
| `clear` | Clear screen |
| `reset` | Clear chat history |
| `exit` | Exit OpenBro |

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
