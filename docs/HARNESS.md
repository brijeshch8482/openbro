# OpenBro Harness v2 — Design

Status: **in progress** (started 2026-11-26).

The harness is everything around the LLM: the tool loop, what goes into
the context, what gets dropped, permissions, memory and retrieval. It must
work the same whichever provider the user picks (API, local GGUF, or a
subscription CLI).

Rules:

- **Tools do one thing and return raw data.** Intelligence lives in the
  model's tool-call loop, not in regex shortcuts.
- **The harness owns the context.** What goes into the prompt, what stays
  out, and when old context is dropped are code decisions, not model ones.
- **Local-first.** Indexes, transcripts and memories live under the user's
  `storage.base_dir`. Only the context needed for one call leaves the machine.
- **Server-ready.** Everything that stores data (documents, memories,
  transcripts) sits behind a backend interface. `storage.backend: local`
  runs it in-process on the laptop; `remote` runs the same interface over
  HTTP against an OpenBro server (`openbro serve`), which itself uses the
  local implementation. Switching is config, not code. See the backend
  section in [RAG.md](RAG.md).

## Data rules for every stored record

So data can move between laptop and server later without a rewrite:

- IDs are UUIDs (or derived from them), never SQLite row numbers.
- Every row has an `owner` (`local` today; a user id on a server).
- Every vector records the embedding model that produced it.
- Every store has a `schema_version` and an export/import bundle format.

---

## Roadmap

Each piece is designed, built and unit-tested on its own. End-to-end
testing happens once all pieces are in.

| # | Piece | Status |
|---|---|---|
| 1 | Core loop fixes — text tool protocol, turn-safe trimming, stable prompt + reminders, audit risk | **built** |
| 2 | Transcripts (JSONL) + compaction | planned |
| 3 | Memory v2 (notes + index) — see [MEMORY.md](MEMORY.md) | designed |
| 4 | Search tools — `grep`, `glob`, `read` (offset/limit), `edit` (exact replace, read-first) | planned |
| 5 | Document RAG — local index, hybrid search, `docs` tool, server-ready backend — see [RAG.md](RAG.md) | designed |
| 6 | Progressive disclosure — tool/skill names in prompt, schemas on demand | planned |
| 7 | Subagents — `task` tool runs a child agent, returns a summary | planned |
| 8 | Hooks + permission rules in `config.yaml` | planned |

---

## Piece 1 — Core loop fixes

### 1a. Text tool-call protocol

**Problem.** The text-only providers (the two subscription CLI providers
and the local specialist provider) return `supports_tools() = False`. Their comments said the agent loop parses text
`<tool_call>` blocks, but no such parser existed. With those providers the
agent sent `tools=None`, got plain text back and ended the turn: OpenBro's
tools, permission gate and audit log never ran.

**Design.** New module `openbro/core/tool_protocol.py`, shared by every
provider without native function calling.

Format (same shape the specialist training data already uses):

```
<tool_call name="file_ops">{"action": "read", "path": "D:/notes.txt"}</tool_call>
```

- `render_tool_instructions(schemas)` → a system-prompt block: the format,
  the rules (one JSON object per call, several calls allowed, stop after
  the calls and wait for results), and every tool with its arguments.
- `parse_tool_calls(text, known_names)` → `(clean_text, calls)`. Accepts
  the `name="…"` form and the name-inside-JSON form
  `<tool_call>{"name": …, "arguments": …}</tool_call>`. Unknown tool names
  are ignored, so prose that mentions the tag can't trigger a call.
- `render_transcript(messages)` → the single prompt string the CLI
  providers send. Earlier tool calls are written back as `<tool_call>`
  tags and tool output as `<tool_result>` blocks, so the model sees its
  own protocol in the history.

Agent behaviour when `provider.supports_tools()` is False:

1. The tool instructions are added to the **system prompt** once.
2. The provider is called with `tools=None`.
3. `<tool_call>` tags in the reply become `response.tool_calls` and go
   through the normal path: risk → permission gate → execute → audit →
   `role="tool"` message → loop.

The CLI providers' own built-in tools are switched off where the CLI
allows it (`--tools ""`), so every action goes through OpenBro's gated,
audited tools. Where a CLI has no such switch it stays in its default
read-only sandbox and the prompt tells it to use OpenBro's tools.

### 1b. Turn-safe history trimming

**Problem.** `_trim_history` kept the last `max_history` messages by
count. The cut could land between an assistant `tool_calls` message and
its `tool` results → orphan `tool` message → HTTP 400 on strict APIs.

**Design.** Group `history[1:]` into turns (a turn starts at a `user`
message). Drop whole turns from the oldest end until the count fits.
The current turn is never dropped. Summarising dropped turns instead of
discarding them is piece 2 (compaction).

### 1c. Stable system prompt + per-turn reminder

**Problem.** The system prompt was rebuilt every turn (workspace, memory
facts, language). A changing first message defeats provider prompt caching
and re-sends the same large block each turn.

**Design.**

| System prompt (built once) | Per-call reminder |
|---|---|
| base prompt, tool list, thinking principles, user environment (paths, drives), text tool protocol when needed | workspace (cwd, git branch, recent files), memory facts, reply language |

The reminder is a `<system-reminder>…</system-reminder>` block appended to
the **latest user message at call time only**. It is not stored in
`history`, so earlier turns stay byte-identical and the cached prefix stays
valid. `refresh_system_prompt()` rebuilds the stable prompt on demand; the
agent calls it automatically when the provider's tool mode changes.

### 1d. Audit log risk

`ToolRegistry.execute()` logged `get_risk(name)` without args, so a
destructive `shell` call was recorded as `moderate`. It now logs the
per-call risk.

### Open decision — playbooks

`_try_playbook` still runs before the LLM (4 built-ins: `geo_lookup`,
`process_check`, `system_health`, `time_now`) even though the README
and the LLM-first refactor describe them as removed. Recommendation:
default `agent.playbooks_enabled` to `false`. Pending the owner's call.

### Not in piece 1

- `stream_chat` still has no tool loop (plain text stream).
- Tool calls in one response still run one after another.
