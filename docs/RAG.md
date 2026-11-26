# OpenBro Document RAG — Design

Status: **v1 built** (2026-11-26). Harness piece 5 — see [HARNESS.md](HARNESS.md).
Server mode (v2) is designed below but not built yet.

OpenBro answers questions over the user's own documents (PDFs, Word,
Excel, scans, notes) without sending the whole library to a model. It
finds the few passages that matter and gives only those to the model.

**Offline first, server-ready.** Version 1 runs entirely on the laptop.
Everything sits behind a backend boundary so the same features can later
run against an OpenBro server by changing config, not code.

---

## 1. Principles

1. **The laptop is the server.** In local mode the index, the uploaded
   files and the embedding model all live under `storage.base_dir`.
   Nothing leaves the machine except the few chunks sent to a cloud model
   for one answer (and nothing at all with a local model).
2. **One boundary, two backends.** The agent, tools and CLI talk to
   interfaces (`DocStore`, `BlobStore`, `Embedder`). `local` implements
   them in-process; `remote` implements them over HTTP. The server itself
   runs the local implementation — one copy of the logic.
3. **Tools return raw data.** `docs.search` returns ranked chunks with
   their source. The model decides whether to search again, read a whole
   document, or answer.
4. **Portable data from day one.** IDs are UUIDs, every row carries an
   `owner`, every vector records the model that made it. Moving an index
   from laptop to server (or back) is an export/import, not a rewrite.

---

## 2. Architecture

```
                     ┌──────────────────────────────────────────────┐
  agent / docs tool  │               DocsService                    │
  /docs CLI commands │  add_source · upload · remove · search · read│
  Telegram bot  ───► │  (ingestion pipeline + hybrid search logic)  │
                     └───────┬──────────────┬──────────────┬────────┘
                             │              │              │
                         DocStore       BlobStore       Embedder
                             │              │              │
             ┌───────────────┴───┐   ┌──────┴──────┐   ┌───┴──────────────┐
  backend:   │ local: SQLite     │   │ local: dir  │   │ local: e5 model  │
  local      │  FTS5 + vectors   │   │ docs/library│   │  on CPU          │
             └───────────────────┘   └─────────────┘   └──────────────────┘
             ┌───────────────────┐   ┌─────────────┐   ┌──────────────────┐
  backend:   │ remote: HTTP      │   │ remote: HTTP│   │ server-side      │
  remote     │  → OpenBro server │   │  upload     │   │  (same model)    │
             └───────────────────┘   └─────────────┘   └──────────────────┘
```

`DocsService` is the only thing callers use. Which backend sits under it
comes from `storage.backend` in config.

### Interfaces (Python protocols)

```python
class DocStore(Protocol):
    def add_source(self, source: Source) -> Source: ...
    def list_sources(self) -> list[Source]: ...
    def remove_source(self, source_id: str) -> None: ...
    def get_document(self, doc_id: str) -> Document | None: ...
    def find_document(self, source_id: str, rel_path: str) -> Document | None: ...
    def upsert_document(self, doc: Document, chunks: list[Chunk]) -> None: ...  # replaces old chunks
    def delete_document(self, doc_id: str) -> None: ...
    def keyword_search(self, query: str, k: int, filters: Filters) -> list[Hit]: ...
    def vector_search(self, vector: list[float], k: int, filters: Filters) -> list[Hit]: ...
    def read_chunks(self, doc_id: str, page: int | None = None) -> list[Chunk]: ...
    def info(self) -> StoreInfo: ...  # counts, size, embed model, schema version

class BlobStore(Protocol):
    def put(self, src_path: Path, name: str) -> BlobRef: ...  # copy into library
    def open(self, ref: BlobRef) -> Path: ...                  # local path to parse
    def delete(self, ref: BlobRef) -> None: ...

class Embedder(Protocol):
    model_id: str
    dim: int
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
```

Hybrid ranking (RRF) lives in `DocsService`, above the boundary, so both
backends rank identically.

---

## 3. Getting files in

Two kinds of input:

| Kind | What happens |
|---|---|
| **Add in place** | File or folder stays where it is. OpenBro indexes it and rescans for changes. |
| **Upload** | OpenBro copies the file into its library (`BlobStore`) and indexes the copy. |

Entry points — **v1 is terminal-first**:

| Way | Command / action | Kind | Phase |
|---|---|---|---|
| Terminal | `/docs add D:/Documents/fees` (drag a file onto the terminal to paste its path) | in place | v1 |
| Chat / voice | "D drive ka fees folder yaad rakh le" → model calls `docs` `action=add` | in place | v1 |
| Library folder | `/docs open` opens `docs/library/` in Explorer; anything copied there is indexed | upload | v1 |
| Telegram | send a PDF or photo to the bot → stored as an upload, then indexed | upload | v2 (server) |

Telegram belongs to server mode. Offline, the bot only answers while the
laptop is awake and OpenBro is running, so it is not a dependable upload
path. Once `storage.backend: remote` is on, the bot talks to the
always-on server: uploads from the phone land in the server library and
are searchable from the phone and the laptop alike.

One-off reads ("ye PDF padh: D:/x.pdf") still go straight through
`document_tool` and store nothing. The index is for collections the user
will ask about again.

Management: `/docs list`, `/docs remove <path|id>`, `/docs reindex`,
`/docs open`, `/docs status`.

---

## 4. Storage layout (local backend)

```
<base_dir>/                      default ~/.openbro, or the user's chosen drive
  docs/
    library/                     uploaded copies (BlobStore)
      receipt-scan.jpg
    index.db                     DocStore (SQLite)
  models/embed/                  local embedding model, downloaded once
```

### `index.db` schema

```sql
meta(key TEXT PRIMARY KEY, value TEXT)
  -- schema_version, embed_model, embed_dim

sources(id TEXT PRIMARY KEY,      -- uuid
        owner TEXT NOT NULL,      -- 'local' now; user id on a server
        kind TEXT NOT NULL,       -- 'folder' | 'file' | 'library'
        path TEXT NOT NULL,       -- absolute path (in place) or library root
        added_at TEXT, last_scan TEXT)

documents(id TEXT PRIMARY KEY,    -- uuid
          owner TEXT NOT NULL,
          source_id TEXT NOT NULL REFERENCES sources(id),
          rel_path TEXT NOT NULL, -- path relative to the source
          title TEXT, mime TEXT,
          sha256 TEXT NOT NULL, mtime REAL, size INTEGER,
          pages INTEGER, indexed_at TEXT,
          status TEXT)            -- 'ok' | 'error: …' | 'skipped: …'

chunks(id TEXT PRIMARY KEY,       -- '<doc_id>:<ord>'
       doc_id TEXT NOT NULL REFERENCES documents(id),
       ord INTEGER, page INTEGER, heading TEXT,
       text TEXT NOT NULL, tokens INTEGER)

chunks_fts USING fts5(text, heading, content='chunks')     -- keyword index
chunk_vectors(chunk_id TEXT PRIMARY KEY, doc_id TEXT, vec BLOB)   -- float32 vectors
```

Original in-place files are never copied or modified.

---

## 5. Indexing pipeline

```
file ─► 1 detect change ─► 2 parse ─► 3 chunk ─► 4 embed ─► 5 store
```

1. **Change detection.** Compare `mtime` and `size` with the stored row;
   if either differs, hash the file (sha256). Same hash → skip. New or
   changed → re-index (old chunks replaced). File gone → delete document.
2. **Parse.** Reuse `tools/document_tool.py` (PDF with OCR fallback, DOCX,
   XLSX, CSV, HTML, images, audio). Page numbers kept where the format
   has them.
3. **Chunk — recursive split.** Split on headings; any piece still over
   `chunk_tokens` (500) splits on paragraphs, then sentences. Neighbouring
   chunks overlap by `chunk_overlap` (50) tokens so a sentence cut at a
   boundary is whole in one of them. Each chunk keeps page and heading.
   Spreadsheets chunk by row blocks with the header row repeated.
4. **Embed.** `intfloat/multilingual-e5-small` (384 dims, CPU, handles
   Hindi). Passages are prefixed `passage: `, queries `query: ` (the
   model's training format). Vectors are L2-normalised so cosine
   similarity is a dot product. Batched (32 chunks per call).
5. **Store.** One transaction per document: delete old chunks, insert
   new chunks, FTS rows and vectors.

Indexing runs in a background job (`core/jobs.py`) with progress events on
the activity bus, so the REPL stays usable while a big folder indexes.

---

## 6. Search algorithm

```
query ─┬─► BM25 keyword search  (top 30) ─┐
       └─► vector search        (top 30) ─┴─► RRF merge ─► top k (default 5)
```

**BM25** (SQLite FTS5 `bm25()`):

```
score(chunk, query) = Σ IDF(w) · f·(k1+1) / (f + k1·(1 − b + b·len/avg_len))
                     w ∈ query
f = term frequency in the chunk; IDF favours rare words;
k1 = 1.2 (repeat saturation); b = 0.75 (length normalisation)
```

Catches exact tokens embeddings miss: invoice numbers, names, romanised
Hinglish ("fee", "INV-2041").

**Vector search** (exact scan with numpy over float32 BLOBs, cached in
memory): the query embedding against every chunk vector, nearest by cosine. 10,000 chunks × 384 floats ≈ 15 MB —
milliseconds, no approximate index needed at personal scale.

**Reciprocal Rank Fusion:**

```
RRF(chunk) = Σ 1 / (60 + rank_in_list)
           lists
```

Uses rank positions only, so BM25 scores and cosine similarities never
need to be put on the same scale. A chunk ranked well by both rises to the
top. Optional later: a cross-encoder reranker over the merged top 20.

---

## 7. The `docs` tool (what the model sees)

| action | args | returns |
|---|---|---|
| `search` | `query`, `k?`, `path_filter?`, `type_filter?` | ranked chunks: text, file, page, heading, score |
| `read` | `doc` (id or path), `page?` | full text of the document or page |
| `add` | `path` | indexing job started + file count |
| `list` | — | sources and document counts |
| `remove` | `path` or id | confirmation |

Raw data only. If the first search misses, the model refines the query or
reads the whole document — that exploration is the reasoning.

---

## 8. Backend switch (offline → server)

### Config

```yaml
storage:
  base_dir: D:/OpenBro-data
  backend: local                 # local | remote
  remote:
    url: https://bro.example.com # OpenBro server
    token_env: OPENBRO_SERVER_TOKEN

rag:
  embedder: local                # local | backend  (remote mode → server embeds)
  embed_model: intfloat/multilingual-e5-small
  chunk_tokens: 500
  chunk_overlap: 50
  top_k: 5
```

### What changes in remote mode

| Step | local | remote |
|---|---|---|
| Upload (library) | copy to `docs/library/` | HTTP upload to the server; server parses, chunks, embeds |
| Add in place | laptop parses + chunks + embeds | laptop parses + chunks (the server can't see laptop folders), **pushes chunks**; server embeds |
| Search | in-process | `POST /v1/search` |
| Embedding | laptop model | server model — one model per index, never mixed |

### Server API (v1)

The server is `openbro serve`: the same `DocsService` over the local
backend, wrapped in HTTP. In local mode no HTTP is involved — the agent
calls `DocsService` directly. Running `openbro serve` on the laptop also
lets the phone or a second machine use the laptop's index.

```
GET    /v1/health                       → version, schema, embed_model, embed_dim
GET    /v1/sources                      → list
POST   /v1/sources                      → register a source (kind, path, owner)
DELETE /v1/sources/{id}
POST   /v1/documents          (multipart file)   → upload + index
PUT    /v1/documents/{id}/chunks  (JSON chunks)  → push pre-parsed chunks
DELETE /v1/documents/{id}
GET    /v1/documents/{id}?page=N        → read
POST   /v1/search   {query, k, filters} → hits
```

Auth: bearer token (from `token_env`). HTTPS only for non-localhost URLs.

### Moving data between backends

- `openbro docs export <file>` / `openbro docs import <file>`: a portable
  bundle (JSONL of sources/documents/chunks + vectors + library files).
  Vectors are reused only when `embed_model` matches; otherwise the
  target re-embeds.
- `openbro docs push` / `pull`: export from one backend, import into the
  other.
- Switching `storage.backend` to `remote` asks for confirmation once,
  stating that document text will now be stored on the server.

### Phases

| Phase | Mode | Scope |
|---|---|---|
| **v1** | offline, terminal-first | local backend; `/docs` commands, chat/voice `docs` tool, library folder; export/import bundle |
| **v2** | server | `openbro serve`, remote backend, `push`/`pull`; Telegram uploads + search through the server |
| later | hybrid | below |

### Later

- **Hybrid mode:** local index as an offline cache, synced with the server.
- **Server storage adapter:** a multi-user server can swap SQLite for a
  database with vector and full-text search (e.g. Postgres + pgvector)
  behind the same `DocStore` protocol. `owner` is already on every row.

---

## 9. Module layout

```
openbro/rag/
  __init__.py
  models.py        Source, Document, Chunk, Hit, Filters, StoreInfo (dataclasses)
  protocols.py     DocStore, BlobStore, Embedder
  service.py       DocsService: ingestion pipeline + hybrid search + RRF
  extract.py       page-aware text extraction (PDF pages, Word headings)
  chunker.py       recursive splitter
  embedder.py      LocalEmbedder (lazy load), factory by config
  local_store.py   SQLite + FTS5 + float32 vector DocStore
  local_blobs.py   library-folder BlobStore
  remote.py        HTTP DocStore / BlobStore (v2)
  __init__.py      build_docs_service(config) → picks local | remote
openbro/tools/docs_tool.py
openbro/server/    `openbro serve` (v2)
```

New optional extra: `pip install 'openbro[rag]'` → `fastembed` (ONNX, no
torch), `pypdf`, `python-docx`. `sentence-transformers` is used instead when
that is what is installed. Without either, `docs` runs keyword-only search
(FTS5 is built into SQLite) and says so. numpy (a core dependency) does
the vector scan, so no SQLite extension is needed.

---

## 10. Test plan

- Chunker: heading/paragraph/sentence splits, overlap, page carry-over,
  spreadsheet header repeat.
- Change detection: new / unchanged / modified / deleted files.
- Local store: upsert replaces chunks; delete cascades; FTS and vector rows
  stay in step.
- RRF: known rank lists → expected order.
- Search end-to-end on a small fixture library (English + Hinglish +
  invoice-number queries), with a fake embedder so CI needs no model.
- Backend contract test: one test suite run against every `DocStore`
  implementation (local now, remote later against a test server).
- Export → import round trip keeps ids, chunks and vectors.

---

## 11. Open decisions

1. Library location: `<base_dir>/docs/library/` (follows the custom drive
   setting) — proposed.
2. Folder rescan: on startup + on `/docs reindex` (v1); live file watching
   later.
3. Max file size for auto-indexing (proposed 200 MB; bigger files need an
   explicit `/docs add`).
