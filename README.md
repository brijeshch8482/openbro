# OpenBro

**Tera Apna AI Bro** - Open-Source Personal AI Agent

> Terminal pe ek command, aur tera personal AI bhai ready hai.

---

## What is OpenBro?

OpenBro is a free, open-source personal AI agent that runs on your machine with a single terminal command. No cloud subscription, no vendor lock-in. Your laptop, your control, your AI bro.

## Features

- **Multi-LLM Support** - Claude, GPT, Groq (free), Ollama (offline)
- **Offline-First** - Works without internet using local models
- **Auto Setup** - Ollama install, model download, everything automatic
- **Terminal CLI** - Rich interactive REPL with Hinglish support
- **Tool Calling** - File ops, shell commands, web search, system info
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
| `tools` | List available tools |
| `storage` | View storage usage and paths |
| `storage move` | Move data to another drive |
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

## Built-in Tools

| Tool | What it does |
|------|-------------|
| `file_ops` | Read, write, list, and search files |
| `shell` | Execute shell commands (with safety blocks) |
| `system_info` | OS, disk, and environment info |
| `web` | Web search (DuckDuckGo) and URL fetching |

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

## Project Status

Currently in **v0.1** - Foundation phase.

## Project Structure

```
openbro/
├── openbro/
│   ├── core/           # Agent brain
│   ├── llm/            # LLM providers (Ollama, Claude, GPT, Groq)
│   ├── tools/          # Built-in tools (file, shell, system, web)
│   ├── channels/       # Input/output (CLI, future: Telegram, Voice)
│   ├── memory/         # Memory system (coming in v0.3)
│   ├── voice/          # Voice layer (coming in v0.5)
│   ├── skills/         # Plugin system (coming in v0.4)
│   ├── cli/            # Terminal REPL + wizard
│   └── utils/          # Config, storage, Ollama setup
├── scripts/            # Install/uninstall scripts
├── tests/              # Test suite
├── pyproject.toml
├── LICENSE (MIT)
└── README.md
```

## License

[MIT](LICENSE) - Free forever. Core will always be open source.

## Author

Built by [Brijesh Chaudhary](https://github.com/brijeshch8482)
