<div align="center">

# OpenBro

**An open-source personal AI agent that runs on your own computer.**

Talk to it in English, Hindi or Hinglish — by terminal, voice or Telegram —
and it does the work: opens apps, manages files, searches your documents,
browses, runs commands, and remembers what matters.

[![CI](https://github.com/brijeshch8482/openbro/actions/workflows/ci.yml/badge.svg)](https://github.com/brijeshch8482/openbro/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.1.0-orange)](CHANGELOG.md)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[Quick start](#quick-start) · [Using OpenBro](#using-openbro) · [Your documents](#your-documents) · [How it works](#how-it-works) · [Safety](#safety-and-control) · [Docs](docs/)

</div>

---

## Why OpenBro

- **Local-first.** Your config, memory, chat history and document index live on your machine. With a local model nothing leaves it at all; with a cloud model only the context needed for one answer is sent.
- **An agent, not a chat window.** It plans and acts through tools — files, apps, shell, Python, web, documents, screenshots, system controls — and keeps going until the task is done.
- **Any model.** Free cloud tiers (Groq, Google Gemini), paid APIs (OpenAI, Anthropic, DeepSeek), subscription CLIs you already use, or a fully offline model running in-process.
- **You stay in charge.** Risky actions ask first. Add your own allow/ask/deny rules and hook scripts. Every tool call is written to an audit log.
- **Speaks your language.** Replies in the language you write in — English, हिंदी or Hinglish — by text or voice.

```text
You > D drive ke fees folder me jo receipts hain unka total bata
Bro: Rs 48,200 across 3 receipts — march.pdf (45,000), lab.pdf (2,400), library.pdf (800).

You > chrome khol aur youtube pe "lofi beats" search kar
Bro: Done — YouTube results for "lofi beats" are open in Chrome.

You > yaad rakh, mujhe short replies pasand hain
Bro: Noted. I'll keep answers short.
```

---

## Quick start

### Install

**Windows (PowerShell)**

```powershell
iwr -useb https://raw.githubusercontent.com/brijeshch8482/openbro/main/scripts/install.ps1 | iex
```

**Linux / macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/brijeshch8482/openbro/main/scripts/install.sh | bash
```

The installer checks for Python 3.10–3.13 (and installs 3.12 if needed), installs OpenBro with its common extras, and starts the setup wizard.

<details>
<summary>Getting an old version from a network cache?</summary>

Pin the script to the latest commit so caches can't serve a stale copy:

```powershell
$sha=(iwr -useb 'https://api.github.com/repos/brijeshch8482/openbro/commits/main'|ConvertFrom-Json).sha; iwr -useb "https://raw.githubusercontent.com/brijeshch8482/openbro/$sha/scripts/install.ps1" | iex
```

```bash
sha=$(curl -fsSL https://api.github.com/repos/brijeshch8482/openbro/commits/main | python3 -c 'import sys,json; print(json.load(sys.stdin)["sha"])') && curl -fsSL "https://raw.githubusercontent.com/brijeshch8482/openbro/$sha/scripts/install.sh" | bash
```
</details>

**With pip**

```bash
pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
  "openbro[all,voice,rag] @ git+https://github.com/brijeshch8482/openbro.git@main"
```

| Extra | Adds |
|---|---|
| `all` | offline model engine, every cloud provider, Telegram, Word/Excel, PDF, document search, web page control |
| `voice` | speech-to-text, text-to-speech, microphone |
| `rag` | meaning-based document search (local embedding model) |
| `browser` | web page control — drives your installed Chrome or Edge |
| `docs` | OCR for scans and images, HTML parsing |
| `office` | Word and Excel only |

**From source**

```bash
git clone https://github.com/brijeshch8482/openbro.git
cd openbro
pip install -e ".[dev,all,voice,rag]"
```

### First run

```bash
openbro
```

The wizard walks you through: where to store data (any drive), which model to use, safety level, personality, voice, Telegram and MCP servers. Re-run it any time with `openbro --setup`.

### Keep it healthy

```bash
openbro doctor        # check Python, config, API key, storage, optional features
openbro update        # update in place — keeps your config, memory and models
openbro uninstall     # remove OpenBro (asks first; --keep-data / --keep-models)
```

`update` keeps the extras you already have, works for pip installs and source checkouts, and supports `--source pypi`, `--extras all,voice,rag` and `--dry-run`. `uninstall` lists every folder and its size before removing anything, and only removes folders that are clearly OpenBro's own.

---

## Using OpenBro

Just type what you want. OpenBro picks the tools, asks before anything risky, and shows each step as it runs.

### Commands inside the chat

| Command | What it does |
|---|---|
| `help` | All commands |
| `tools` | Tools and their risk level |
| `model list` · `model switch <name>` | See and switch models (cloud or offline) |
| `docs add <path>` · `docs search <query>` | Index and search your documents — [details](#your-documents) |
| `memory` · `memory open` · `memory edit` | What OpenBro remembers · open the notes folder · edit your standing instructions |
| `remember <key> <value>` · `forget <name>` | Save or delete a memory yourself |
| `context` · `compact [focus]` | Context usage · summarise older turns now |
| `sessions` | Past sessions (resume with `openbro --resume`) |
| `boss` / `boss off` | Ask before every tool call |
| `voice on` / `voice off` | Talk instead of type |
| `show` / `hide` · `activity` · `audit` | Live activity panel · recent events · tool audit log |
| `workspace` · `storage` · `skills` | Current folder context · storage locations · plugins |
| `reset` · `clear` · `exit` | New conversation · clear screen · quit |

### Commands in your shell

```bash
openbro                         # start chatting
openbro --resume                # continue the last session (exactly where you left off)
openbro ask "kitne PDF desktop pe hain?"   # one question, no chat
openbro --voice                 # voice mode
openbro --telegram              # run as your Telegram bot
openbro --offline               # force the local model
openbro config set <key> <value>
openbro model download llama3.2:3b
openbro mcp status
openbro doctor | update | uninstall
```

---

## Your documents

Point OpenBro at your folders and ask questions about what's inside them.

```text
You > docs add D:/Documents
✓ D:/Documents: 214 new, 0 updated, 0 unchanged (3 skipped)

You > is saal ki fee receipts me total kitna diya?
Bro: Rs 48,200 (fees/march.pdf p.1, fees/lab.pdf p.1, fees/library.pdf p.2)
```

- **Formats:** PDF (scans via OCR), Word, Excel, CSV, Markdown, text, HTML, JSON/YAML, code, and images with OCR.
- **Search:** keyword search (exact names, numbers like `INV-2041`, Hinglish words) **plus** meaning-based search ("payment acknowledgement" matches "fee receipt"), merged into one ranked list. Without the `rag` extra it runs on keywords alone.
- **Stays current:** only new or changed files are re-indexed; deleted files drop out. Folders are re-scanned in the background when OpenBro starts.
- **Your files are never moved or changed.** Folders are indexed in place. `docs upload <file>` copies a file into OpenBro's own library instead.
- **Answers cite their source** — file, page and heading.
- **Portable:** `docs export <file>` and `docs import <file>` move the whole index to another machine.

| Command | |
|---|---|
| `docs add <path>` | Index a file or folder in place |
| `docs upload <file>` | Copy into the library and index |
| `docs list` · `docs status` | What's indexed · index details |
| `docs search <query>` | Search from the prompt |
| `docs remove <path>` | Stop using a folder (files untouched) |
| `docs reindex` | Re-scan everything |
| `docs open` | Open the library folder |

The index is one SQLite file at `<storage>/docs/index.db`. Design: [docs/RAG.md](docs/RAG.md).

---

## Web pages

OpenBro can operate websites for you in a real browser window — search inside a site, fill a form, follow links, read what's behind the navigation.

```text
You > flipkart pe 20000 ke andar best rated earbuds dhoondh, top 3 bata
Bro: Top rated under Rs 20,000: … (opened the results, sorted by rating, read the first page)
```

- It sees each page as a numbered list of links, buttons and fields, acts by number, and gets the updated page back after every step.
- It uses the Chrome or Edge already on your computer, with its **own profile** (under your storage folder), so sites stay logged in between sessions without touching your everyday browser.
- **It asks before anything that matters:** clicking buy / pay / send / delete / submit, or typing into a password field, needs your OK. Captchas and OTPs are handed to you.
- Want it hidden? `openbro config set browser.headless true`.

---

## Web demo

`openbro web` serves a chat page where anyone can talk to OpenBro in a browser and watch it think and call tools live — useful for showing the project to people who won't install it.

```bash
pip install "openbro[web,rag]"
export GROQ_API_KEY=gsk_...        # free key from console.groq.com (or use your configured provider)
openbro web --port 8000            # http://127.0.0.1:8000
```

The page is public, so visitors get a **locked-down agent**:

| Visitors can | Visitors can't |
|---|---|
| Search the web and read public pages | Run commands, read or write files, open apps |
| Search the demo documents (README + `docs/` by default, or `--docs <path>`) | Add or remove documents, reach internal network addresses |
| Ask about dates and times | See your memory, `OPENBRO.md`, folders or hooks |

Each visitor has a separate, unsaved conversation. Limits keep a free API key alive: 20 messages per IP per hour (`--per-ip`), 300 per hour in total, 25 turns per chat, 1,500 characters per message, 4 answers at a time, and short tool results. Behind a hosting proxy, add `--trust-proxy` so limits use the visitor's real IP.

**Put it online:** a free Hugging Face Space or any container host — step by step in [docs/DEPLOY.md](docs/DEPLOY.md). The `Dockerfile` bakes in the embedding model and the demo index, so the page is ready seconds after it starts.

---

## Memory and instructions

**Memories** are small notes OpenBro keeps about you and your work — who you are, how you like replies, ongoing projects, where things live. Each is a Markdown file you can read and edit (`memory open`). OpenBro saves them when you tell it something lasting or correct it, updates instead of duplicating, and never stores passwords. Each turn it brings in the notes relevant to what you asked.

**Instructions** are rules you write yourself in `OPENBRO.md`:

- `~/.openbro/OPENBRO.md` — applies everywhere (`memory edit` opens it)
- `OPENBRO.md` in a project folder — applies when you work there

```markdown
# OPENBRO.md
- Reply in Hinglish, 1–3 lines.
- My code lives on D:/code. Never touch C:/Windows.
```

**Long sessions don't forget.** When a conversation gets long, older turns are summarised (goal, what's done, decisions, open items, key paths) instead of being dropped, and the full transcript — every tool call included — is saved, so `--resume` brings back exactly what was there.

---

## How it works

OpenBro wraps the model in a **harness**: the code that decides what the model sees, runs its tools, asks your permission, and keeps context under control.

```
your message
 → hooks (optional) → stable system prompt + per-turn reminder
   (your folder, relevant memories, reply language)
 → model ⇄ tools loop
     each tool call: hooks → your rules → permission mode → run → audit log
 → long history? summarise older turns
 → final answer (a stop hook can ask it to keep going)
```

| Part | What it gives you |
|---|---|
| Tool loop | Works with native function calling **and** with text-only models (tool calls written as tags and parsed) |
| Context | Stable, cache-friendly system prompt; per-turn facts sent separately; token budget with summarisation |
| Transcripts | Every message saved, tool calls included; lossless resume |
| Memory | One note per memory, ranked per turn; `OPENBRO.md` instructions |
| Search tools | `grep`, `glob`, `read` (line ranges), `edit` (exact replace, must read first) |
| Documents | Hybrid keyword + meaning search over your files |
| Lean requests | Rarely used and plugin tools are listed by name and loaded on demand |
| Sub-agents | `task` hands a big search to a helper with a fresh context and gets back only the report |
| Control | Permission rules and hook scripts from your config |

Full design: [docs/HARNESS.md](docs/HARNESS.md).

---

## Tools

| Tool | Does | Risk |
|---|---|---|
| `file_ops` | List, read, write, search and open files | moderate |
| `grep` · `glob` · `read` | Search file contents · find files by name · read line ranges | safe |
| `edit` | Exact text replacement in a file it has read | moderate |
| `document` | Read any file: PDF, Word, Excel, images (OCR), audio (transcript) | safe |
| `docs` | Search your indexed documents | safe (add/remove: moderate) |
| `python` | Run Python for calculations, data, automation | moderate |
| `shell` · `elevate` | Run commands · run one as administrator (asks via the OS prompt) | moderate, dangerous for destructive commands · dangerous |
| `app` · `process` | Open/close apps · find and stop processes | moderate |
| `webpage` | Operate web pages in a real browser: open, click, type, choose, scroll, read, screenshot, tabs | safe to look · moderate to act · dangerous for buy/pay/send/delete and passwords |
| `browser` · `web` · `download` | Open a site for you · fetch pages / search · download files | moderate / safe |
| `screenshot` · `clipboard` · `system_control` | Capture screen · copy/paste · volume, lock, sleep | safe / dangerous |
| `word` · `excel` | Create and edit Office files | moderate |
| `system_info` · `network` · `datetime` | Disk, RAM, OS · IP, ping, DNS · time and dates | safe |
| `memory` | Save, read, search memories | safe |
| `task` | Hand a job to a sub-agent | safe (its calls are checked as usual) |
| `tool_search` | Load a deferred tool when needed | safe |
| `cli_agent` · `sticky_notes` · `notification` | Delegate to a coding CLI · Sticky Notes · desktop notifications | loaded on demand |

Plus your **skills** (GitHub, YouTube, Gmail, Calendar, Notion — or your own in `~/.openbro/skills/`) and **MCP servers** (`mcp.servers` in config).

---

## Safety and control

**Permission modes** — `safety.permission_mode`
- `normal` (default): asks before dangerous actions
- `boss`: asks before every action (`boss` in the chat)
- `auto`: never asks (use with rules)

The shell tool judges each command: reading is moderate, deleting is dangerous.

**Rules** — decided before the mode, `deny` > `ask` > `allow`:

```yaml
safety:
  rules:
    allow: ["shell(git status*)", "grep", "read"]
    ask:   ["shell(*pip install*)"]
    deny:  ["shell(*Remove-Item*C:/Windows*)", "elevate"]
```

**Hooks** — your own scripts at fixed points. Each gets the event as JSON on stdin; exit code `2` blocks the action (or, for `stop`, makes the agent keep working).

```yaml
hooks:
  pre_tool_use:
    - matcher: "shell"
      command: "python D:/hooks/check_shell.py"
  post_tool_use: []
  user_prompt_submit: []
  stop: []
```

**Audit log** — every tool call with its arguments, risk and result: `audit` in the chat, or `~/.openbro/audit.log`.

---

## Models

| Provider | Cost | Setup |
|---|---|---|
| **Local (offline)** | free | the wizard downloads a model; runs in-process |
| Groq | free tier | key from console.groq.com |
| Google Gemini | free tier | key from aistudio.google.com |
| OpenAI | paid | key from platform.openai.com |
| Anthropic | paid | key from console.anthropic.com |
| DeepSeek | low cost | key from platform.deepseek.com |
| Subscription CLIs (`claude`, `codex`) | your existing plan | install and sign in to the CLI |

Set a fallback so a failed call retries on another model: `openbro config set llm.fallback local`.

**Offline models** (GGUF, Q4_K_M): `llama3.2:3b` (2 GB, recommended), `llama3.2:1b` (0.8 GB, low-end PCs), `llama3.1:8b`, `mistral:7b`, `mistral-nemo`, `phi3:mini`, `gemma2:2b`, `gemma2:9b` and more — `openbro model list`. Import a file you already have with `openbro model import <file.gguf>`.

---

## Voice, phone and more

**Voice** — `openbro --voice` or `voice on`. Offline speech recognition, natural Indian English and Hindi voices, continuous listening (or a wake word: `voice.mode wake_word`). Permission questions are asked out loud: answer *haan* or *nahi*.

**Telegram** — chat from your phone: set `channels.telegram.token` and `allowed_users`, then `openbro --telegram`. Each user gets separate memory; dangerous tools are blocked there.

**Skills and MCP** — `skills` lists plugins; drop your own in `~/.openbro/skills/<name>/skill.py`. MCP servers configured in `mcp.servers` add their tools automatically.

**Coding CLIs** — "ask the coding CLI to add a test for X": `cli_agent` drives a coding CLI installed on your machine, streams its progress, and enforces a per-call and daily cost cap (`safety.cli_agent`).

**Your own model** — `openbro train` (maintainers) builds a small fine-tuned `openbro.gguf`; `openbro model update` pulls the latest. See [docs/TRAINING.md](docs/TRAINING.md).

---

## Configuration

Everything lives in `~/.openbro/config.yaml`. Use `openbro config show | get | set`.

| Key | Default | |
|---|---|---|
| `llm.provider` · `llm.model` · `llm.fallback` | from the wizard | model choice |
| `storage.base_dir` · `storage.models_dir` | `~/.openbro` | where data and models live |
| `storage.backend` | `local` | `remote` is reserved for the upcoming OpenBro server |
| `safety.permission_mode` · `safety.rules` | `normal` · none | [Safety](#safety-and-control) |
| `hooks.*` | none | [Hooks](#safety-and-control) |
| `memory.max_context_tokens` | `24000` | context budget before summarising |
| `memory.auto_extract` | `off` | at `exit`, let the model propose memory updates (`on` / `auto`) |
| `rag.embedder` · `rag.embed_model` | `auto` · `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | document search |
| `agent.tool_loading` · `agent.deferred_tools` | `auto` · `cli_agent, sticky_notes, notification` | which tools load on demand |
| `agent.subagent_max_steps` | `15` | step cap for sub-agents |
| `browser.headless` · `browser.channel` | `false` · `auto` | show the browser window; `auto` = Chrome, then Edge, then Playwright's own |
| `agent.playbooks_enabled` | `true` | instant answers for a few fixed questions |

---

## Storage and privacy

```
~/.openbro/                 (or the drive you picked)
  config.yaml               settings and API keys
  OPENBRO.md                your instructions
  memory/memory.db          transcripts and sessions
  memory/notes/<user>/      memories, one .md each
  docs/index.db             document index
  docs/library/             uploaded copies
  browser-profile/          the webpage tool's own browser profile (stays logged in)
  models/                   offline models
  audit.log · logs/         tool audit and activity log
```

Nothing is collected or sent anywhere except to the model provider you chose, and then only the context for that one request. With a local model, nothing leaves your machine. `storage move` relocates everything to another drive.

---

## Troubleshooting

Start with `openbro doctor` — it points at most problems directly.

| Problem | Fix |
|---|---|
| "API key invalid" | `openbro config set providers.<provider>.api_key <key>` |
| Rate limit on a free tier | wait a minute, `model switch` to another model, or set `llm.fallback` |
| First offline reply is slow | the first load of a model takes 30–90 s; `llama3.2:3b` is the best everyday choice on CPU |
| Document search says "keyword only" | `pip install 'openbro[rag]'`, then `docs reindex` |
| `webpage` can't start a browser | install Chrome or Edge, or run `python -m playwright install chromium` |
| Voice doesn't hear you | Windows: Settings → Privacy → Microphone → allow desktop apps; run `voice test` |
| `llama-cpp-python` tries to compile | install with `--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu` (the installer and `openbro update` do this) |

---

## Development

```bash
pip install -e ".[dev]"
ruff check openbro tests && ruff format --check openbro tests
pytest
```

Every change goes through a pull request; CI runs lint and the full test suite on Ubuntu and Windows with Python 3.10–3.13 before it is merged.

```
openbro/
  core/          agent loop, permissions, rules, hooks, tool protocol
  memory/        transcripts, compaction, notes, instructions
  rag/           document index and search
  tools/         built-in tools
  llm/           model providers and fallback
  cli/           chat, wizard, doctor / update / uninstall
  voice/ channels/ skills/ mcp/ orchestration/ training/
docs/            HARNESS.md · RAG.md · MEMORY.md · ARCHITECTURE.md · TRAINING.md
scripts/         installers and uninstallers
tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

## Roadmap

- [x] Agent harness v2 — tool protocol, transcripts, compaction, memory, search tools, documents, lean requests, sub-agents, rules and hooks
- [x] Offline document search (v1)
- [x] Web page control — open, click, type and read pages in a real browser
- [ ] OpenBro server — `storage.backend: remote`, phone uploads through Telegram, shared index
- [ ] Docs site and skill marketplace

## License

[MIT](LICENSE) — free forever.

Built by [Brijesh Chaudhary](https://github.com/brijeshch8482).
