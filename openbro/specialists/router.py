"""Two-tier router that maps a user prompt to a category slug.

Tier 1 — rule match: split each category's `keywords` field on '|',
do a case-insensitive substring check against the prompt. Most
specific match wins (longest matching keyword + deepest tree node).
Cheap, deterministic, no model required.

Tier 2 — embedding fallback: if no rule matches confidently, embed
the prompt with a tiny sentence-transformer and pick the category
whose name+description embedding is the cosine-nearest. Cached
embeddings live alongside the DB.

The router never raises — it always returns *some* category, falling
through to 'general' if nothing else fits. Production-grade behaviour
is judged by accuracy + latency, both measurable.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openbro.specialists import CATEGORIES_DB, EMBEDDINGS_FILE


@dataclass
class RouteResult:
    """Result of routing one prompt."""

    slug: str
    display_name: str
    confidence: float
    method: str  # 'rule' | 'embedding' | 'fallback'
    elapsed_ms: float
    matched_keyword: str | None = None


class Router:
    """Loads categories once and routes prompts thereafter."""

    def __init__(self, db_path: str = CATEGORIES_DB):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._categories = self._load_categories()
        self._embeddings: np.ndarray | None = None
        self._embedder = None  # lazy-loaded only if rules miss

    # ─── Setup ─────────────────────────────────────────────────────

    def _load_categories(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT id, parent_id, slug, display_name, description, keywords "
                "FROM categories ORDER BY id"
            )
        )

    def _depth(self, cat_id: int) -> int:
        """Walk parent chain — leaves score higher than top-level."""
        d = 0
        cur = cat_id
        for row in self._categories:
            if row["id"] == cur and row["parent_id"]:
                d += 1
                cur = row["parent_id"]
        return d

    # ─── Tier 1: rule match ────────────────────────────────────────

    def _rule_match(self, prompt: str) -> RouteResult | None:
        text = prompt.lower()
        # Score every (category, keyword) hit, then pick the longest
        # keyword (most specific) at deepest tree level.
        best: tuple[int, int, sqlite3.Row, str] | None = None
        for row in self._categories:
            keywords = row["keywords"] or ""
            if not keywords:
                continue
            for kw in keywords.split("|"):
                kw = kw.strip().lower()
                if not kw or kw not in text:
                    continue
                depth = sum(
                    1 for r in self._categories if r["id"] == row["parent_id"]
                )  # rough depth proxy
                score = (len(kw), depth)
                if best is None or score > (best[0], best[1]):
                    best = (len(kw), depth, row, kw)
        if best is None:
            return None
        _, _, row, kw = best
        return RouteResult(
            slug=row["slug"],
            display_name=row["display_name"],
            confidence=0.9,  # rule hits are high-confidence by definition
            method="rule",
            elapsed_ms=0,
            matched_keyword=kw,
        )

    # ─── Tier 2: embedding fallback ────────────────────────────────

    def _ensure_embedder(self):
        if self._embedder is not None:
            return
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    def _ensure_category_embeddings(self) -> np.ndarray:
        if self._embeddings is not None:
            return self._embeddings
        cache = Path(EMBEDDINGS_FILE)
        if cache.exists():
            self._embeddings = np.load(cache)
            return self._embeddings
        self._ensure_embedder()
        texts = [f"{r['display_name']}: {r['description']}" for r in self._categories]
        vec = self._embedder.encode(texts, normalize_embeddings=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, vec)
        self._embeddings = vec
        return vec

    def _embedding_match(self, prompt: str) -> RouteResult:
        self._ensure_embedder()
        cat_vec = self._ensure_category_embeddings()
        prompt_vec = self._embedder.encode([prompt], normalize_embeddings=True)
        scores = (cat_vec @ prompt_vec.T).flatten()
        idx = int(scores.argmax())
        row = self._categories[idx]
        return RouteResult(
            slug=row["slug"],
            display_name=row["display_name"],
            confidence=float(scores[idx]),
            method="embedding",
            elapsed_ms=0,
        )

    # ─── Public ────────────────────────────────────────────────────

    def route(self, prompt: str) -> RouteResult:
        """Return the category for `prompt`. Always returns something."""
        t0 = time.time()
        rule = self._rule_match(prompt)
        if rule is not None:
            rule.elapsed_ms = (time.time() - t0) * 1000
            return rule
        try:
            emb = self._embedding_match(prompt)
        except Exception:
            # Fall through if sentence-transformers / cache misbehaves.
            row = next(r for r in self._categories if r["slug"] == "general")
            return RouteResult(
                slug=row["slug"],
                display_name=row["display_name"],
                confidence=0.0,
                method="fallback",
                elapsed_ms=(time.time() - t0) * 1000,
            )
        emb.elapsed_ms = (time.time() - t0) * 1000
        return emb

    def adapter_path_for(self, slug: str) -> str | None:
        """Look up the LoRA adapter for a slug, walking parents if the
        leaf has no specialist trained yet."""
        cur = next((r for r in self._categories if r["slug"] == slug), None)
        while cur is not None:
            adapter = self._conn.execute(
                "SELECT adapter_path FROM categories WHERE id = ?",
                (cur["id"],),
            ).fetchone()
            if adapter and adapter["adapter_path"]:
                return adapter["adapter_path"]
            if cur["parent_id"] is None:
                return None
            cur = next(
                (r for r in self._categories if r["id"] == cur["parent_id"]),
                None,
            )
        return None
