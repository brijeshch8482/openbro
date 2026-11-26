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
| 2 | Lossless transcripts + compaction (summarise, don't drop) | **built** |
| 3 | Memory v2 — notes, ranked memory block, OPENBRO.md, memory tool — see [MEMORY.md](MEMORY.md) | **built** |
| 4 | Search tools — `grep`, `glob`, `read` (offset/limit), `edit` (exact replace, read-first) | **built** |
| 5 | Document RAG — local index, hybrid search, `docs` tool, server-ready backend — see [RAG.md](RAG.md) | **built** (v1) |
| 6 | Progressive disclosure — tool/skill names in prompt, schemas on demand | **built** |
| 7 | Subagents — `task` tool runs a child agent, returns a summary | **built** |
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

---

## Piece 2 — Lossless transcripts + compaction

Implements L2 and L3 of [MEMORY.md](MEMORY.md).

### Problems

- The `conversations` table stored only user/assistant text. Tool calls
  and tool results were lost, so `--resume` brought back a history where
  the model could not see what it had done.
- When history grew past `agent.max_history` messages, old turns were
  **deleted** — the model forgot the start of a long task with no trace.
- Size was counted in messages, not tokens. One `file_ops read` of a big
  file could blow the context while the message count looked fine.

### 2a. Transcript store (L3)

`openbro/memory/transcript.py`

```python
class TranscriptStore(Protocol):
    def append(self, session_id, owner, channel, msg: Message) -> str  # returns message uid
    def load(self, session_id, owner) -> Transcript                   # summary + messages after it
    def set_summary(self, session_id, owner, summary, upto_uid) -> None
```

- `SQLiteTranscriptStore` (local backend) uses the existing `memory.db`.
  Migration adds `uid` (uuid), `tool_calls` (JSON) and `tool_call_id` to
  `conversations`; old rows keep working. `user_id` is the `owner` column.
  `schema_meta.transcript_schema = 2`.
- Every message the agent appends is recorded: user, assistant (with its
  tool calls), and `tool` results. The uid is set on `Message.id`.
- The rolling summary lives on the session row
  (`sessions.metadata = {"summary": …, "summary_upto": <uid>}`).
- `build_transcript_store(config)` picks the backend from
  `storage.backend`; `remote` arrives with the server (v2).

### 2b. Compaction (L2)

`openbro/memory/compaction.py` — replaces `Agent._trim_history`.

History shape: `[system prompt] [summary (optional)] turn, turn, …`.
A turn starts at a `user` message; cuts only ever happen between turns, so
tool-call pairs stay intact by construction.

Runs at the start of every turn and before every model call inside the
tool loop. Triggers when **either**

- estimated tokens > `trigger_ratio` (0.75) × budget, or
- messages > `agent.max_history` (kept as a count trigger).

Budget = `memory.max_context_tokens` (24K), lowered to the local model's
`n_ctx` when that is smaller. Native tool schemas (~6K tokens) go out
with every call but are not in the history, so their size is reserved out
of the budget. Token estimate: characters / 4.

Stages, each only if still over:

1. **Clear old tool output** (no model call). In turns older than the last
   3, tool results over 1,000 chars become
   `[tool output cleared — 12,400 chars from file_ops]`. The transcript
   keeps the full text.
2. **Summarise** every turn except the most recent ones (up to 6, fewer
   if those alone are over) with one model call, no tools, ≤ 800 tokens,
   fixed sections: *Goal · Done · Decisions · Open items · Key facts &
   paths*. A previous summary is merged in (rolling). Tool output is cut
   to 1,500 chars per result in the summariser's input. If the call fails,
   a deterministic summary (the user's requests, shortened) is used — the
   turn never crashes.
3. **Shrink the current turn** if it alone is over: keep its user message
   and the newest complete tool-call groups; a note on the user message
   says how many earlier steps were dropped.

The summary is a `system` message right after the system prompt, marked
`[CONVERSATION_SUMMARY]`. It changes only when compaction runs, so the
cached prefix (piece 1) breaks at most once per compaction. It is also
saved to the session row with the uid of the last message it covers.

Event: `context compacted: 31,200 → 8,900 tokens (summarised 9 turns)`.

### 2c. Resume

`--resume` loads the saved summary plus every message after
`summary_upto` — tool calls and results included — then runs
`repair_tool_pairs` (drops a `tool_calls` message whose results are
missing, e.g. after a crash mid-turn, and any orphan `tool` result) and
compaction. Sessions saved before this change load as plain text, as
before.

### 2d. REPL

- `compact [focus]` — compact now; the optional focus text tells the
  summariser what to keep.
- `context` — tokens used vs budget, summary size, turns and messages.

### Config

```yaml
memory:
  max_context_tokens: 24000
  compaction:
    trigger_ratio: 0.75
    keep_recent_turns: 6
    clear_tool_output_after_turns: 3
    tool_output_keep_chars: 1000
    summary_max_tokens: 800
storage:
  backend: local        # local | remote (v2)
```

### Not in piece 2

- Full-text search over transcripts (`memory.search` over past chats) —
  piece 3, with the memory tool.

---

## Piece 3 — Memory v2

Implements L0 and L1 of [MEMORY.md](MEMORY.md); the transcript search part
of L3 comes with the memory tool.

| Part | Where | What it does |
|---|---|---|
| Notes | `memory/notes.py` | One `.md` file per memory with frontmatter (`id`, `name`, `description`, `type`, `scope`, dates) in `<storage>/memory/notes/<owner>/`. `INDEX.md` is generated by code. Behind `NoteStore`; `FileNoteStore` is the local backend. |
| Memory block | `notes.memory_block` | Per turn, in the reminder (not the stable prompt). Small memory → every note inlined. Larger → index lines only: `user` + `feedback` always, then project notes for this repo and the best BM25 matches for the user's message, within `memory.index_budget_tokens` (1,500); `(+N more — use search)` when cut. |
| Ranking | `memory/ranking.py` | Pure-Python BM25 over name + description + body. Notes are few, so no index has to be kept in sync. |
| Instructions | `memory/instructions.py` | `OPENBRO.md` from the config dir, then from the project root (nearest `.git`) down to the cwd. In the stable system prompt, capped at 4K tokens; the prompt is rebuilt when a file changes or the cwd moves. |
| Memory tool | `tools/memory_tool.py` | `save` (upsert by name) · `read` · `delete` · `list` · `search` (notes + past messages). Old `remember / recall / forget` still work. |
| Principle 9 | agent prompt | When to save, update instead of duplicating, never save secrets. |
| Migration | `MemoryManager.notes` | Old key/value facts are copied into `user` notes once; the old manager API (`remember`, `recall`, `context_prompt`) now runs on notes. |
| Session end | `Agent.end_session` | Optional (`memory.auto_extract: off` by default, `on`, or `auto` = cloud/CLI only): one model call proposes save/delete ops as JSON when the user types `exit`. |
| REPL | `memory`, `memory open`, `memory edit` | List notes · open the notes folder · edit the global `OPENBRO.md`. |

Differences from the MEMORY.md proposal: ranking is in-process BM25
instead of an FTS5 table (no sync needed at this size); auto-extract
defaults to off so `exit` never waits on a model call unless the user
opts in.

---

## Piece 4 — Search tools

`openbro/tools/search_tools.py`. Retrieval over files is done by the model
calling these tools in a loop (grep → read the hits → edit), not by an
index. Each returns raw data.

| Tool | Risk | Does |
|---|---|---|
| `grep` | safe | Regex over file contents. `output_mode` content (`file:line:text`, optional context lines) · files · count; `glob` filter; case-insensitive flag. Uses ripgrep when installed, else a Python walk. Skips `.git`, `node_modules`, build/cache folders, binary files and files over 5 MB. |
| `glob` | safe | Files by name pattern (`**/*.py`), newest first, same skip list. |
| `read` | safe | Numbered lines with `offset` / `limit`; tells the model the offset to continue from. Text files only (PDF/Office → `document`). Records the file's mtime. |
| `edit` | moderate | Exact string replacement. Refused unless the file was `read` in this session and is unchanged since; `old_string` must be unique unless `replace_all`. Keeps CRLF line endings. New files → `file_ops` write. |

Output is capped at 30,000 characters with a note saying what was cut.
The four names were added to the Groq inline tool-call rescue list.

---

## Piece 5 — Document RAG (v1)

Full design in [RAG.md](RAG.md). Built: the local backend, terminal-first.

- `openbro/rag/`: `DocsService` (ingest + hybrid search + RRF, above the
  storage boundary), `SQLiteDocStore` (FTS5 + float32 vectors in
  `<base_dir>/docs/index.db`), `LibraryBlobStore` (`docs/library/`),
  `LocalEmbedder` (lazy; fastembed or sentence-transformers; none →
  keyword-only), `extract.py`, `chunker.py`.
- Incremental scans: size + mtime, then sha256; changed files re-indexed,
  deleted files dropped. Indexed sources are re-scanned in the background
  when the REPL starts.
- Embedding model recorded in the index; a different model switches
  search to keyword-only until `docs reindex` re-embeds.
- `docs` tool: search / read / add / list / remove (add and remove are
  moderate risk).
- REPL: `docs status | add | upload | list | search | remove | reindex |
  open | export | import`.
- Portable: `docs export` writes a JSONL bundle (+ the library folder);
  `docs import` merges it, re-embedding when the model differs.
- Not yet: the remote backend and Telegram uploads (server, v2).

---

## Piece 6 — Progressive disclosure

Every tool schema is sent with every call (~6K tokens for the built-ins,
more with skills and MCP servers). Deferred tools are listed in the
stable system prompt as one line each; their schemas are sent only after
the model loads them.

- `ToolRegistry.configure_loading(config)` — `agent.tool_loading`:
  - `auto` (default): defers every extension tool (skills, MCP) plus
    `agent.deferred_tools` (default `cli_agent`, `sticky_notes`,
    `notification` — large and rarely needed).
  - `all`: no deferral, as before.
  Common built-ins stay loaded on purpose: some providers reject a call
  to a tool that is not in the request, so a tool the user reaches for
  every day must not sit behind a load step.
- `tool_search` (`tools/tool_search.py`): `select='a,b'` loads by name,
  `query='…'` ranks deferred tools with BM25. Returns the loaded tools'
  parameters as text; from the next model call their schemas are in the
  request (the agent refreshes the tool list on every loop step).
- A deferred tool called directly (text-protocol providers) is loaded and
  run.
- The system prompt's `MORE TOOLS` block is built once, so the cached
  prefix stays stable.

---

## Piece 7 — Sub-agents

`tools/task_tool.py`. The `task` tool hands a self-contained job to a
child `Agent` that starts with a fresh context and returns only its final
report. Broad searches (dozens of grep/read results) stay out of the main
history; the parent sees one compact tool result.

- Types: `explore` (default) gets only safe-risk tools, minus `memory`
  and `task`; `general` gets every tool except `task` (no recursion).
- The child shares the parent's provider and **permission gate** — a
  risky action inside a sub-agent asks the user exactly like one in the
  main agent.
- Tools come from `ToolRegistry.view(allowed)`: same tool instances,
  filtered, with deferred/loaded state copied and `tool_search` bound to
  the view (loading inside the child doesn't change the parent).
- The child's messages go to an `InMemoryTranscriptStore` — never into the
  session transcript. Its token use is added to the parent's counters.
- Its system prompt adds a sub-agent role: work until done, report with
  exact paths/numbers, no questions back.
- Step cap: `agent.subagent_max_steps` (15); at the cap the usual forced
  no-tools final call produces the report.
- REPL shows `▸ sub-agent (explore) started: …` / `◂ … done: N tool calls`.
