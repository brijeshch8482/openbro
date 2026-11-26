"""Document RAG (harness piece 5) — see docs/RAG.md.

`get_docs_service()` returns the process-wide DocsService for the
configured backend (`storage.backend`: local now, remote with the server).
"""

from __future__ import annotations

import threading
from pathlib import Path

from openbro.rag.service import DocsService, RagConfig

_service: DocsService | None = None
_lock = threading.Lock()


def build_docs_service(config: dict | None = None) -> DocsService:
    from openbro.rag.embedder import build_embedder
    from openbro.rag.local_blobs import LibraryBlobStore
    from openbro.rag.local_store import SQLiteDocStore

    config = config or {}
    backend = (config.get("storage", {}) or {}).get("backend", "local")
    if backend == "remote":
        raise NotImplementedError(
            "storage.backend 'remote' needs the OpenBro server (planned for v2)."
        )
    if backend != "local":
        raise ValueError(f"Unknown storage.backend: {backend!r}")
    base = Path((config.get("storage", {}) or {}).get("base_dir") or Path.home() / ".openbro")
    docs_dir = base / "docs"
    return DocsService(
        store=SQLiteDocStore(docs_dir / "index.db"),
        blobs=LibraryBlobStore(docs_dir / "library"),
        embedder=build_embedder(config),
        config=RagConfig.from_config(config),
    )


def get_docs_service() -> DocsService:
    global _service
    with _lock:
        if _service is None:
            from openbro.utils.config import load_config

            _service = build_docs_service(load_config())
        return _service


def reset_docs_service() -> None:
    global _service
    with _lock:
        _service = None


__all__ = ["DocsService", "RagConfig", "build_docs_service", "get_docs_service"]
