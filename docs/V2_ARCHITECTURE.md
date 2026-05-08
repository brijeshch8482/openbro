# OpenBro v2 — "True JARVIS"

> A self-improving autonomous agent with persistent memory, self-coding, and continuous learning.

## Vision

OpenBro v1 was a **tool router**: LLM decided everything, OpenBro forwarded calls.

OpenBro v2 is a **true agent**: OpenBro thinks, plans, and learns. The LLM is one capability among many — used for reasoning, but not for control. OpenBro itself is the brain.

## Five pillars

### 1. The Brain — `~/.openbro/brain/`

A portable, updatable, learnable file structure that contains everything OpenBro knows about its user and the world.

```
~/.openbro/brain/
├── profile.yaml         # User model (language, style, projects, schedule)
├── memory.db            # SQLite-vec — semantic memory + past patterns
├── skills/              # Auto-generated executable Python workflows
│   ├── organize_downloads.py
│   ├── morning_briefing.py
│   └── send_daily_report.py
├── world.json           # Static facts: PC paths, installed apps, network state
├── learnings.jsonl      # Append-only log of every learning event
└── meta.json            # version, last_update, brain_id
```

**Properties**
- **Portable**: `tar -czf brain.tar.gz ~/.openbro/brain/` → restore on any machine, same personality.
- **Updatable**: `brain update` pulls community patterns from `github.com/openbro/openbro-brain`.
- **Inspectable**: every file is plain text or queryable SQLite — user can audit.
- **Local-first**: nothing leaves the machine unless user opts in.

### 2. Reasoning Pipeline — LLM is 30% of the brain

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
- Smaller LLM for planner/verifier (cheap, fast)
- Big LLM only for the actual reasoning step

### 3. Self-Coding Engine — `openbro/brain/self_coder.py`

For any task without a built-in tool or learned skill:
1. Brain searches for similar past patterns
2. If none, LLM generates Python code
3. Code runs in a sandbox (subprocess, restricted env, no admin)
4. Result captured + verified
5. On success: code saved as a new skill in `brain/skills/`
6. Next time same task: skill runs directly, no LLM call

**Sandbox guarantees**
- Subprocess only (never `exec()` or `eval()` in main process)
- `OPENBRO_SANDBOX=1` env var enforces:
  - No `os.system`, no shell metacharacters
  - Network calls require permission (Boss mode integration)
  - File writes confined to user-allowed paths
  - 60-second timeout per execution
- Boss mode: dangerous patterns require user approval before save-as-skill

### 4. Latest LLM Auto-Select — `openbro/llm/auto_select.py`

OpenBro never hardcodes a model name. On startup and on `brain update`:

```python
def best_available_llm():
    # Probe Groq cloud → newest available (currently llama-3.3-70b)
    # Probe Ollama local → installed models, pick highest-version
    # Probe Anthropic if user has key → newest Claude
    # Probe OpenAI if user has key → newest GPT
    # Score by: capability × user preference (cloud/local/cost) × tool-calling ability
    return best_match
```

When new models drop (e.g. `llama3.3:8b` released), OpenBro notices on next `brain update` and offers: *"Naya model available, switch karu? Better at tool calling."*

### 5. Continuous Learning — `openbro/brain/reflection.py`

Reflection runs after every interaction (background thread, low priority):

```python
def reflect(interaction):
    # interaction = {prompt, response, tools_used, follow_up, latency, errors}
    
    # Extract patterns:
    if user_followup_was_correction():
        brain.flag_pattern_as_low_confidence()
    elif user_followup_was_thanks():
        brain.boost_pattern_confidence()
    
    # Update profile:
    brain.profile.update_language_stats(detected_language)
    brain.profile.update_active_projects(mentioned_projects)
    
    # If task succeeded with novel approach: save as skill
    if successful and self_coded:
        brain.skills.save(generated_code)
    
    # Memory hygiene: low-confidence + old → decay
    brain.memory.compact()
```

User can inspect any time: `brain stats`, `brain learnings`, `brain skills`.

## Module structure

```
openbro/
├── brain/                    # NEW — the v2 intelligence layer
│   ├── __init__.py
│   ├── core.py               # Brain class — main orchestrator
│   ├── profile.py            # User profile (yaml-backed)
│   ├── memory.py             # Semantic memory (sqlite-vec + sentence-transformers)
│   ├── skills.py             # Skill registry + executor
│   ├── self_coder.py         # Code-and-run engine
│   ├── reflection.py         # Learning loop
│   ├── updater.py            # Community sync
│   ├── storage.py            # Directory layout + path helpers
│   └── world.py              # Static-facts module
├── llm/
│   ├── auto_select.py        # NEW — dynamic best-model picker
│   └── ...                   # existing providers
├── core/
│   ├── reasoning.py          # NEW — pipeline orchestrator
│   └── agent.py              # MODIFIED — uses Brain + reasoning pipeline
└── ...                       # rest unchanged
```

## CLI additions

```
You > brain                  # Show brain stats
You > brain export           # Backup to tar.gz
You > brain import <file>    # Restore
You > brain update           # Pull community patterns
You > brain skills           # List learned skills
You > brain learnings        # Recent learning events
You > brain reset            # Wipe brain (with confirmation)
```

## Migration from v1

When a v1 user runs v2 first time:
1. Detect `~/.openbro/config.yaml` from v1
2. Create `~/.openbro/brain/` from v1's facts table + recent messages
3. Generate initial profile from past chat patterns
4. Keep v1's config, skills, channels — just bolt on the brain

No data loss. v1 → v2 migration is automatic on first run.

## Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Self-coding executes arbitrary code | Sandboxed subprocess, no admin, restricted env, Boss mode permission required |
| Negative learning loops (agent learns wrong patterns) | Confidence scoring per pattern; low-confidence flagged for human review |
| Brain bloat over time | Periodic compaction job; old low-confidence patterns decay |
| LLM auto-select picks bad model | User override; capability score thresholds |
| Privacy (brain has personal data) | Local-only by default; community upload is opt-in with redaction |
| Brain merge conflicts (community pull) | Per-user namespace + 3-way merge with explicit conflict resolution |

## Build order

1. **Brain Profile + Storage** — foundational module, no LLM dependency
2. **Semantic Memory** — sentence-transformers + sqlite-vec
3. **Skills system** — registry + executor + sample skills
4. **Self-coding engine** — sandbox + LLM code-gen + skill auto-save
5. **Latest LLM auto-select** — provider-probing logic
6. **Reasoning pipeline** — planner → executor → verifier loop
7. **Reflection loop** — background learning thread
8. **Brain updater** — community sync
9. **Agent integration** — modify core/agent.py to use Brain
10. **CLI commands** — `brain *` family of commands

## Status

| Component | Status |
|-----------|--------|
| Brain Profile + Storage | 🚧 In progress |
| Semantic Memory | ⬜ Pending |
| Skills system | ⬜ Pending |
| Self-coding engine | ⬜ Pending |
| Latest LLM auto-select | ⬜ Pending |
| Reasoning pipeline | ⬜ Pending |
| Reflection loop | ⬜ Pending |
| Brain updater | ⬜ Pending |
| Agent integration | ⬜ Pending |
| CLI commands | ⬜ Pending |
