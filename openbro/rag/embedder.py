"""Embedders — turn text into vectors for meaning-based search.

The default model, `intfloat/multilingual-e5-small` (384 dims), handles
English and Hindi on a CPU. It is loaded lazily, through `fastembed`
(ONNX, no torch) if installed, otherwise `sentence-transformers`. With
neither installed, `build_embedder` returns None and search runs on
keywords (BM25) alone.

e5 models expect `query: ` / `passage: ` prefixes; they are added here.
Vectors are L2-normalised so cosine similarity is a plain dot product.
"""

from __future__ import annotations

import math
import threading
from typing import Protocol

DEFAULT_MODEL = "intfloat/multilingual-e5-small"


class Embedder(Protocol):
    model_id: str
    dim: int

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def _normalise(vec) -> list[float]:
    values = [float(x) for x in vec]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


class LocalEmbedder:
    """Lazy wrapper over fastembed or sentence-transformers."""

    def __init__(self, model_id: str = DEFAULT_MODEL, backend: str = "auto"):
        self.model_id = model_id
        self._backend = backend
        self._model = None
        self._kind = ""
        self._lock = threading.Lock()
        self.dim = 0

    @staticmethod
    def available() -> bool:
        for mod in ("fastembed", "sentence_transformers"):
            try:
                __import__(mod)
                return True
            except ImportError:
                continue
        return False

    def _load(self):
        with self._lock:
            if self._model is not None:
                return
            errors = []
            if self._backend in ("auto", "fastembed"):
                try:
                    from fastembed import TextEmbedding  # type: ignore[import-not-found]

                    self._model = TextEmbedding(model_name=self.model_id)
                    self._kind = "fastembed"
                except Exception as e:  # not installed, or model not in its list
                    errors.append(f"fastembed: {e}")
            if self._model is None and self._backend in ("auto", "sentence-transformers"):
                try:
                    from sentence_transformers import (  # type: ignore[import-not-found]
                        SentenceTransformer,
                    )

                    self._model = SentenceTransformer(self.model_id)
                    self._kind = "st"
                except Exception as e:
                    errors.append(f"sentence-transformers: {e}")
            if self._model is None:
                raise RuntimeError("no embedding backend could load: " + "; ".join(errors))
            self.dim = len(self._encode(["probe"])[0])

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if self._kind == "fastembed":
            return [_normalise(v) for v in self._model.embed(texts)]
        return [_normalise(v) for v in self._model.encode(texts, batch_size=32)]

    def _prefix(self, kind: str, text: str) -> str:
        return f"{kind}: {text}" if "e5" in self.model_id.lower() else text

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        self._load()
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            out.extend(self._encode([self._prefix("passage", t) for t in texts[i : i + 32]]))
        return out

    def embed_query(self, text: str) -> list[float]:
        self._load()
        return self._encode([self._prefix("query", text)])[0]


def build_embedder(config: dict | None) -> Embedder | None:
    """`rag.embedder`: auto (default) | local | none."""
    rag = (config or {}).get("rag", {}) or {}
    mode = str(rag.get("embedder", "auto")).lower()
    if mode == "none":
        return None
    if mode == "auto" and not LocalEmbedder.available():
        return None
    return LocalEmbedder(str(rag.get("embed_model", DEFAULT_MODEL)))
