"""Semantic memory — embed past interactions, search by meaning.

Uses sentence-transformers (small model, ~80 MB) for embeddings + SQLite for
storage. Falls back to keyword search if sentence-transformers isn't installed.

Public API:
    mem = SemanticMemory(db_path)
    mem.add(text, kind="user", meta={"session": "abc"})
    hits = mem.search("query text", limit=5)   # returns [{text, score, ...}]
    mem.compact(keep_recent_days=180)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from openbro.core.activity import get_bus

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMB_DIM = 384  # all-MiniLM-L6-v2


class SemanticMemory:
    def __init__(self, db_path: Path, model_name: str = DEFAULT_MODEL):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self._model = None  # lazy-loaded
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    meta TEXT,
                    embedding BLOB
                )"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory(kind)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_memory_ts ON memory(ts)")

    # ─── embedding (lazy) ──────────────────────────────────────────

    def _get_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            # sentence-transformers is optional; without it, fall back to
            # keyword-based search. To enable semantic search:
            #   pip install 'openbro[brain]'
            return None
        try:
            self._model = SentenceTransformer(self.model_name)
            return self._model
        except Exception:
            return None

    def _embed(self, text: str) -> bytes | None:
        model = self._get_model()
        if model is None:
            return None
        try:
            import numpy as np

            vec = model.encode(text, convert_to_numpy=True, show_progress_bar=False)
            return np.asarray(vec, dtype="float32").tobytes()
        except Exception:
            return None

    @staticmethod
    def _cosine(a: bytes, b: bytes) -> float:
        try:
            import numpy as np

            va = np.frombuffer(a, dtype="float32")
            vb = np.frombuffer(b, dtype="float32")
            denom = (np.linalg.norm(va) * np.linalg.norm(vb)) or 1e-8
            return float(np.dot(va, vb) / denom)
        except Exception:
            return 0.0

    # ─── public API ────────────────────────────────────────────────

    def add(self, text: str, kind: str = "user", meta: dict | None = None) -> int:
        if not text or not text.strip():
            return -1
        emb = self._embed(text)
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute(
                "INSERT INTO memory (ts, kind, text, meta, embedding) VALUES (?, ?, ?, ?, ?)",
                (time.time(), kind, text, json.dumps(meta or {}), emb),
            )
            return cur.lastrowid or -1

    def search(self, query: str, limit: int = 5, kind: str | None = None) -> list[dict]:
        """Semantic-first search; falls back to keyword if no embeddings."""
        if not query or not query.strip():
            return []
        q_emb = self._embed(query)

        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            sql = "SELECT id, ts, kind, text, meta, embedding FROM memory"
            params: list = []
            if kind:
                sql += " WHERE kind = ?"
                params.append(kind)
            sql += " ORDER BY ts DESC LIMIT 1000"
            rows = list(con.execute(sql, params))

        if not rows:
            return []

        # Semantic ranking
        if q_emb is not None:
            scored = []
            for r in rows:
                emb = r["embedding"]
                score = self._cosine(q_emb, emb) if emb else 0.0
                if score > 0.2:  # weak threshold
                    scored.append((score, r))
            scored.sort(key=lambda s: -s[0])
            top = scored[:limit]
            return [self._row_to_dict(r, score) for score, r in top]

        # Keyword fallback
        q_lower = query.lower()
        scored_kw = []
        for r in rows:
            text = (r["text"] or "").lower()
            if not text:
                continue
            # Score: count of query words that appear
            words = [w for w in q_lower.split() if len(w) > 2]
            hits = sum(1 for w in words if w in text)
            if hits > 0:
                scored_kw.append((hits, r))
        scored_kw.sort(key=lambda s: -s[0])
        return [self._row_to_dict(r, hits) for hits, r in scored_kw[:limit]]

    @staticmethod
    def _row_to_dict(row, score: float) -> dict:
        try:
            meta = json.loads(row["meta"]) if row["meta"] else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        return {
            "id": row["id"],
            "ts": row["ts"],
            "kind": row["kind"],
            "text": row["text"],
            "meta": meta,
            "score": float(score),
        }

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as con:
            return int(con.execute("SELECT COUNT(*) FROM memory").fetchone()[0])

    def compact(self, keep_recent_days: int = 180) -> int:
        """Drop entries older than N days. Returns number removed."""
        cutoff = time.time() - (keep_recent_days * 86400)
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute("DELETE FROM memory WHERE ts < ?", (cutoff,))
            removed = cur.rowcount
        get_bus().emit("brain", f"memory compacted: dropped {removed} old entries")
        return removed

    def context_for(self, query: str, limit: int = 5) -> str:
        """Build a string ready to inject into LLM prompt."""
        hits = self.search(query, limit=limit)
        if not hits:
            return ""
        lines = ["Relevant past memory:"]
        for h in hits:
            kind = h["kind"]
            text = h["text"][:200].replace("\n", " ")
            lines.append(f"  [{kind}] {text}")
        return "\n".join(lines)
