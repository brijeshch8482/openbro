# OpenBro Architecture — True JARVIS

> **Mission**: A self-improving autonomous personal AI agent that thinks, plans, executes, learns, and gets smarter every day. Best-in-class at every layer.

## Core principles

1. **OpenBro is the brain. The LLM is one tool.** Reasoning happens in OpenBro; LLM is called for specific neural-net-only jobs (language, code generation, classification).
2. **Best-in-class everywhere.** Optimization, reasoning, problem-solving, sandbox, voice, UI — no compromises.
3. **Self-improving.** Every interaction teaches the agent. The brain gets smarter, faster, more personal over time.
4. **User is boss.** Capability is unlimited; the user grants/revokes permission. No artificial restrictions.
5. **Local-first, cloud-optional.** Privacy by default; cloud LLMs available when user opts in.
6. **Always current.** Latest open-source LLM auto-detected. Model picker upgrades as new releases drop.

## Six pillars

### 1. The Brain — `~/.openbro/brain/`

Portable, updatable, learnable knowledge store.

```
~/.openbro/brain/
├── profile.yaml         # User model: language, style, projects, schedule, expertise
├── memory.db            # SQLite-vec — semantic memory + past patterns
├── skills/              # Auto-generated executable Python workflows
│   ├── organize_downloads.py
│   ├── morning_briefing.py
│   └── send_daily_report.py
├── world.json           # Static facts: PC paths, installed apps, network state
├── learnings.jsonl      # Append-only log of every learning event
└── meta.json            # version, brain_id, last_update
```

**Properties**
- **Portable**: `tar -czf brain.tar.gz ~/.openbro/brain/` → restore on any machine, same personality.
- **Updatable**: `brain update` pulls community patterns from `github.com/openbro/openbro-brain`.
- **Inspectable**: every file is plain text or queryable SQLite — user can audit.
- **Local-first**: nothing leaves the machine unless user opts in.

### 2. Reasoning Pipeline

Every user prompt flows through:

```
User prompt
    ↓
[Brain Recall]      ← semantic search past memories + patterns
    ↓
[Context Builder]   ← inject relevant memory + system state + fresh web data
    ↓
[Skill Match]       ← exact skill exists? execute directly (no LLM call)
    ↓ if no
[Planner]           ← small LLM call: break into steps
    ↓
[Executor]          ← run steps; LLM for reasoning at each step
    ↓
[Verifier]          ← small LLM call: validate result
    ↓
[Reflector]         ← save learnings to brain
    ↓
User reply
```

LLM cost drops because:
- Known skills run directly (zero LLM)
- Smaller LLM for planner / verifier
- Big LLM only for the actual reasoning step

### 3. Self-Coding Engine — `openbro/brain/self_coder.py`

For any task without a built-in tool or learned skill:

1. Brain searches for similar past patterns
2. If none, LLM generates Python code
3. Code runs in a sandbox process (configurable; see Pillar 6)
4. Result captured + verified
5. On success: code saved as a new skill in `brain/skills/`
6. Next time the same task: skill runs directly, no LLM call

The agent literally **writes new tools for itself** as it encounters new tasks.

### 4. Latest LLM Auto-Select — `openbro/llm/auto_select.py`

OpenBro never hardcodes a model. On startup and on `brain update`:

```python
def best_available_llm():
    # Probe Groq cloud → newest available
    # Probe Ollama local → installed models, pick highest-version
    # Probe Anthropic / OpenAI if user has keys
    # Score by: capability × user preference × tool-calling ability
    return best_match
```

When new models drop (e.g. `llama3.4:8b`), OpenBro notices on next `brain update` and offers: *"Naya model available, switch karu? Better at tool calling."*

### 5. Custom UI — not a CLI

OpenBro ships a **dedicated graphical interface** — not a terminal prompt.

**Default UI**: lightweight web app served at `http://localhost:8765`, auto-opens in the user's browser.

```
+----------------------------------------------------------+
|  OpenBro                              [voice] [settings] |
+----------------------------------------------------------+
|                                                          |
|   You      : Mere desktop pe images organize kar         |
|              ────────────────────────────                |
|   Brain    : • search memory: similar task 3 weeks back  |
|              • skill match: organize_files               |
|              • running...                                 |
|              + 47 images sorted into Images/             |
|   OpenBro  : Bhai, 47 images move kar di Desktop/Images/ |
|                                                          |
|  ────────────────────────────────────────────────────    |
|  [🎤]  [Type or speak...                          ] [→] |
+----------------------------------------------------------+
```

**Why a browser UI**:
- Cross-platform without bundling Electron (no 100 MB overhead)
- Native voice via Web Audio API (no PortAudio install issues)
- Beautiful by default — Tailwind / clean dark theme
- Activity panel + chat in one place
- Hotkey to summon (system tray app to toggle visibility)

**Stack**: FastAPI backend + WebSocket streaming + vanilla JS frontend (no framework dependency, fast load).

### 6. Sandbox-on-Demand

OpenBro is **trusted by default**. The user is the boss; the agent should be capable of doing anything the user asks.

Sandboxing is **opt-in per command**, not always-on:

```
You > "Yeh script run kar"               # Trusted - runs directly
You > "Sandbox me yeh script run kar"    # User asks for sandbox
   → spawned in restricted subprocess
   → no admin, restricted env, network gated
```

Or via flag:
```
You > /safe Yeh untrusted code run kar
```

Or via Boss mode (already exists):
```
You > boss   # Every action requires permission, sandbox auto-on
```

Default: full capability. Sandbox: when explicitly requested or when reflection flags untrusted code (e.g. self-coded skill from community).

## Module structure

```
openbro/
├── brain/                    # The intelligence layer
│   ├── __init__.py           # Public API: Brain
│   ├── core.py               # Brain class - main orchestrator
│   ├── profile.py            # User profile (yaml-backed)
│   ├── memory.py             # Semantic memory (sqlite-vec + sentence-transformers)
│   ├── skills.py             # Skill registry + executor
│   ├── self_coder.py         # Code-and-run engine (sandbox optional)
│   ├── reflection.py         # Learning loop
│   ├── updater.py            # Community sync
│   ├── storage.py            # Directory layout + path helpers
│   └── world.py              # Static facts about the user's environment
├── llm/
│   ├── auto_select.py        # Dynamic best-model picker
│   └── ...                   # existing providers
├── core/
│   ├── reasoning.py          # Pipeline orchestrator
│   └── agent.py              # Agent integration with Brain
├── ui/                       # Custom GUI (replaces CLI as default surface)
│   ├── server.py             # FastAPI + WebSocket
│   ├── frontend/             # Static HTML/CSS/JS
│   │   ├── index.html
│   │   ├── style.css
│   │   └── app.js
│   └── tray.py               # System tray app (hotkey + show/hide)
└── ...                       # rest unchanged
```

## CLI commands (still available, just not the primary surface)

```
openbro                       # Launch GUI (default)
openbro --cli                 # Launch terminal REPL (legacy)
openbro --tray                # Background tray app + GUI on hotkey
openbro --voice               # Voice-only mode
openbro --setup               # Re-run setup wizard
```

Inside any UI:
```
brain                         # Show brain stats
brain export                  # Backup
brain import <file>           # Restore
brain update                  # Pull community patterns
brain skills                  # List learned skills
brain learnings               # Recent learning events
brain reset                   # Wipe (with confirmation)
```

## Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Self-coding executes arbitrary code | Sandbox on demand; safe-by-default for community-pulled skills; user-coded trusted by default |
| Negative learning loops | Confidence scoring per pattern; low-confidence flagged for review |
| Brain bloat over time | Periodic compaction; old low-confidence patterns decay |
| LLM auto-select picks bad model | User override; capability score thresholds |
| Privacy (brain has personal data) | Local-only by default; community upload is opt-in with redaction |
| Brain merge conflicts (community pull) | Per-user namespace + 3-way merge with explicit conflict resolution |

## Status

| Component | Status |
|-----------|--------|
| Brain Profile + Storage | ✅ Foundation shipped |
| Semantic Memory | ⬜ Next |
| Skills system | ⬜ Next |
| Self-coding engine | ⬜ Next |
| Latest LLM auto-select | ⬜ Next |
| Reasoning pipeline | ⬜ Next |
| Reflection loop | ⬜ Next |
| Brain updater | ⬜ Next |
| Custom GUI | ⬜ Next |
| Sandbox-on-demand | ⬜ Next |
| Agent integration | ⬜ Next |
