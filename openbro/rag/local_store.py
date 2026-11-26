"""Local DocStore: one SQLite file (`<base_dir>/docs/index.db`).

- keyword search: FTS5 with BM25 ranking (built into SQLite)
- vector search: float32 vectors stored as BLOBs, exact cosine scan with
  numpy (personal scale: 10K chunks × 384 dims ≈ 15 MB, milliseconds)

Every row carries `owner`; ids are uuids; `meta` records the schema
version and the embedding model, so an index can move to a server.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from openbro.memory.ranking import tokenize
from openbro.rag.models import Chunk, Document, Filters, Hit, Source, StoreInfo

SCHEMA_VERSION = 1


class SQLiteDocStore:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._vec_cache: tuple[list[str], np.ndarray] | None = None
        self._init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    path TEXT NOT NULL, added_at TEXT, last_scan TEXT);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    rel_path TEXT NOT NULL, title TEXT, mime TEXT,
                    sha256 TEXT NOT NULL, mtime REAL, size INTEGER, pages INTEGER,
                    indexed_at TEXT, status TEXT,
                    UNIQUE(source_id, rel_path));
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    ord INTEGER, page INTEGER, heading TEXT, text TEXT NOT NULL,
                    tokens INTEGER);
                CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
                CREATE TABLE IF NOT EXISTS chunk_vectors (
                    chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                    doc_id TEXT NOT NULL, vec BLOB NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED, heading, text, tokenize = 'unicode61');
                """
            )
            c.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    # ─── meta ────────────────────────────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ─── sources ─────────────────────────────────────────────────────

    def add_source(self, source: Source) -> Source:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM sources WHERE owner = ? AND path = ?", (source.owner, source.path)
            ).fetchone()
            if row:
                return _source(row)
            c.execute(
                "INSERT INTO sources (id, owner, kind, path, added_at, last_scan) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source.id, source.owner, source.kind, source.path, source.added_at, None),
            )
        return source

    def list_sources(self, owner: str = "local") -> list[Source]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM sources WHERE owner = ? ORDER BY added_at", (owner,)
            ).fetchall()
        return [_source(r) for r in rows]

    def mark_scanned(self, source_id: str, when: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE sources SET last_scan = ? WHERE id = ?", (when, source_id))

    def remove_source(self, source_id: str) -> None:
        with self._lock, self._conn() as c:
            ids = [
                r["id"]
                for r in c.execute("SELECT id FROM documents WHERE source_id = ?", (source_id,))
            ]
            for doc_id in ids:
                self._delete_doc_rows(c, doc_id)
            c.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            self._vec_cache = None

    # ─── documents ───────────────────────────────────────────────────

    def get_document(self, doc_id: str) -> Document | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return _document(row) if row else None

    def find_document(self, source_id: str, rel_path: str) -> Document | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM documents WHERE source_id = ? AND rel_path = ?",
                (source_id, rel_path),
            ).fetchone()
        return _document(row) if row else None

    def list_documents(self, source_id: str) -> list[Document]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM documents WHERE source_id = ? ORDER BY rel_path", (source_id,)
            ).fetchall()
        return [_document(r) for r in rows]

    def upsert_document(
        self,
        doc: Document,
        chunks: list[Chunk],
        vectors: list[list[float]] | None = None,
    ) -> None:
        """Replace a document and all its chunks in one transaction."""
        with self._lock, self._conn() as c:
            old = c.execute(
                "SELECT id FROM documents WHERE source_id = ? AND rel_path = ?",
                (doc.source_id, doc.rel_path),
            ).fetchone()
            if old:
                doc.id = old["id"]
                self._delete_doc_rows(c, doc.id)
            c.execute(
                """INSERT INTO documents (id, owner, source_id, rel_path, title, mime, sha256,
                       mtime, size, pages, indexed_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc.id,
                    doc.owner,
                    doc.source_id,
                    doc.rel_path,
                    doc.title,
                    doc.mime,
                    doc.sha256,
                    doc.mtime,
                    doc.size,
                    doc.pages,
                    doc.indexed_at,
                    doc.status,
                ),
            )
            for ch in chunks:
                ch.doc_id = doc.id
                c.execute(
                    "INSERT INTO chunks (id, doc_id, ord, page, heading, text, tokens) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ch.id, doc.id, ch.ord, ch.page, ch.heading, ch.text, ch.tokens),
                )
                c.execute(
                    "INSERT INTO chunks_fts (chunk_id, heading, text) VALUES (?, ?, ?)",
                    (ch.id, ch.heading, ch.text),
                )
            if vectors:
                for ch, vec in zip(chunks, vectors):
                    c.execute(
                        "INSERT INTO chunk_vectors (chunk_id, doc_id, vec) VALUES (?, ?, ?)",
                        (ch.id, doc.id, np.asarray(vec, dtype=np.float32).tobytes()),
                    )
            self._vec_cache = None

    def delete_document(self, doc_id: str) -> None:
        with self._lock, self._conn() as c:
            self._delete_doc_rows(c, doc_id)
            self._vec_cache = None

    @staticmethod
    def _delete_doc_rows(c: sqlite3.Connection, doc_id: str) -> None:
        c.execute(
            "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id = ?)",
            (doc_id,),
        )
        c.execute("DELETE FROM chunk_vectors WHERE doc_id = ?", (doc_id,))
        c.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def clear_vectors(self) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM chunk_vectors")
            self._vec_cache = None

    def chunks_without_vectors(self, limit: int = 256) -> list[Chunk]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM chunks WHERE id NOT IN (SELECT chunk_id FROM chunk_vectors) LIMIT ?",
                (limit,),
            ).fetchall()
        return [_chunk(r) for r in rows]

    def add_vectors(self, pairs: list[tuple[Chunk, list[float]]]) -> None:
        with self._lock, self._conn() as c:
            for ch, vec in pairs:
                c.execute(
                    "INSERT OR REPLACE INTO chunk_vectors (chunk_id, doc_id, vec) VALUES (?, ?, ?)",
                    (ch.id, ch.doc_id, np.asarray(vec, dtype=np.float32).tobytes()),
                )
            self._vec_cache = None

    def read_chunks(self, doc_id: str, page: int | None = None) -> list[Chunk]:
        with self._conn() as c:
            if page is None:
                rows = c.execute(
                    "SELECT * FROM chunks WHERE doc_id = ? ORDER BY ord", (doc_id,)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM chunks WHERE doc_id = ? AND page = ? ORDER BY ord",
                    (doc_id, page),
                ).fetchall()
        return [_chunk(r) for r in rows]

    # ─── search ──────────────────────────────────────────────────────

    def _hit_rows(self, c, chunk_ids: list[str]) -> dict[str, sqlite3.Row]:
        if not chunk_ids:
            return {}
        marks = ",".join("?" * len(chunk_ids))
        rows = c.execute(
            f"""SELECT ch.id, ch.doc_id, ch.text, ch.page, ch.heading, d.rel_path, d.owner,
                       s.path AS source_path, s.kind AS source_kind
                FROM chunks ch JOIN documents d ON d.id = ch.doc_id
                JOIN sources s ON s.id = d.source_id
                WHERE ch.id IN ({marks})""",
            chunk_ids,
        ).fetchall()
        return {r["id"]: r for r in rows}

    @staticmethod
    def _passes(row: sqlite3.Row, filters: Filters) -> bool:
        if row["owner"] != filters.owner:
            return False
        path = _abs_path(row)
        if filters.path_contains and filters.path_contains.lower() not in path.lower():
            return False
        if filters.extensions and Path(path).suffix.lower() not in filters.extensions:
            return False
        return True

    def keyword_search(self, query: str, k: int, filters: Filters) -> list[Hit]:
        terms = tokenize(query)
        if not terms:
            return []
        match = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
        with self._conn() as c:
            rows = c.execute(
                "SELECT chunk_id, bm25(chunks_fts) AS score FROM chunks_fts "
                "WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
                (match, k * 4),
            ).fetchall()
            info = self._hit_rows(c, [r["chunk_id"] for r in rows])
        hits = []
        for r in rows:
            row = info.get(r["chunk_id"])
            if row is None or not self._passes(row, filters):
                continue
            hits.append(_hit(row, -float(r["score"])))
            if len(hits) >= k:
                break
        return hits

    def _vectors(self) -> tuple[list[str], np.ndarray]:
        with self._lock:
            if self._vec_cache is None:
                with self._conn() as c:
                    rows = c.execute("SELECT chunk_id, vec FROM chunk_vectors").fetchall()
                ids = [r["chunk_id"] for r in rows]
                if rows:
                    matrix = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
                else:
                    matrix = np.zeros((0, 0), dtype=np.float32)
                self._vec_cache = (ids, matrix)
            return self._vec_cache

    def vector_search(self, vector: list[float], k: int, filters: Filters) -> list[Hit]:
        ids, matrix = self._vectors()
        if not ids:
            return []
        q = np.asarray(vector, dtype=np.float32)
        if matrix.shape[1] != q.shape[0]:
            return []  # index built with another model — needs reindex
        scores = matrix @ q
        order = np.argsort(-scores)[: k * 4]
        with self._conn() as c:
            info = self._hit_rows(c, [ids[i] for i in order])
        hits = []
        for i in order:
            row = info.get(ids[i])
            if row is None or not self._passes(row, filters):
                continue
            hits.append(_hit(row, float(scores[i])))
            if len(hits) >= k:
                break
        return hits

    # ─── info / export ───────────────────────────────────────────────

    def info(self) -> StoreInfo:
        with self._conn() as c:
            count = lambda t: c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]  # noqa: E731
            sources, docs, chunks, vecs = (
                count("sources"),
                count("documents"),
                count("chunks"),
                count("chunk_vectors"),
            )
        dim = self.get_meta("embed_dim")
        return StoreInfo(
            sources=sources,
            documents=docs,
            chunks=chunks,
            vectors=vecs,
            embed_model=self.get_meta("embed_model"),
            embed_dim=int(dim) if dim else None,
            schema_version=int(self.get_meta("schema_version") or SCHEMA_VERSION),
            size_bytes=self.path.stat().st_size if self.path.exists() else 0,
        )

    def dump(self):
        """Every row, for export bundles."""
        with self._conn() as c:
            for table in ("meta", "sources", "documents", "chunks"):
                for row in c.execute(f"SELECT * FROM {table}"):
                    yield table, dict(row)
            for row in c.execute("SELECT chunk_id, doc_id, vec FROM chunk_vectors"):
                yield (
                    "chunk_vectors",
                    {
                        "chunk_id": row["chunk_id"],
                        "doc_id": row["doc_id"],
                        "vec": np.frombuffer(row["vec"], dtype=np.float32).tolist(),
                    },
                )

    def load_rows(self, table: str, row: dict) -> None:
        with self._lock, self._conn() as c:
            if table == "meta":
                c.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (row["key"], row["value"]),
                )
            elif table == "chunk_vectors":
                c.execute(
                    "INSERT OR REPLACE INTO chunk_vectors (chunk_id, doc_id, vec) VALUES (?, ?, ?)",
                    (
                        row["chunk_id"],
                        row["doc_id"],
                        np.asarray(row["vec"], dtype=np.float32).tobytes(),
                    ),
                )
            elif table in ("sources", "documents", "chunks"):
                cols = ",".join(row)
                marks = ",".join("?" * len(row))
                c.execute(
                    f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", list(row.values())
                )
                if table == "chunks":
                    c.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (row["id"],))
                    c.execute(
                        "INSERT INTO chunks_fts (chunk_id, heading, text) VALUES (?, ?, ?)",
                        (row["id"], row.get("heading") or "", row["text"]),
                    )
            self._vec_cache = None


def _abs_path(row) -> str:
    if row["source_kind"] == "file":
        return row["source_path"]
    return str(Path(row["source_path"]) / row["rel_path"])


def _hit(row, score: float) -> Hit:
    return Hit(
        chunk_id=row["id"],
        doc_id=row["doc_id"],
        text=row["text"],
        page=row["page"],
        heading=row["heading"] or "",
        path=_abs_path(row),
        score=score,
    )


def _source(r) -> Source:
    return Source(
        kind=r["kind"],
        path=r["path"],
        owner=r["owner"],
        id=r["id"],
        added_at=r["added_at"],
        last_scan=r["last_scan"],
    )


def _document(r) -> Document:
    return Document(
        source_id=r["source_id"],
        rel_path=r["rel_path"],
        sha256=r["sha256"],
        mtime=r["mtime"],
        size=r["size"],
        owner=r["owner"],
        title=r["title"] or "",
        mime=r["mime"] or "",
        pages=r["pages"],
        status=r["status"] or "ok",
        id=r["id"],
        indexed_at=r["indexed_at"],
    )


def _chunk(r) -> Chunk:
    return Chunk(
        doc_id=r["doc_id"],
        ord=r["ord"],
        text=r["text"],
        page=r["page"],
        heading=r["heading"] or "",
        tokens=r["tokens"] or 0,
    )
