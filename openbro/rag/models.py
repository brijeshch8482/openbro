"""Data types shared by the RAG service and its storage backends."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Source:
    """A folder/file added in place, or the uploads library."""

    kind: str  # 'folder' | 'file' | 'library'
    path: str  # absolute path
    owner: str = "local"
    id: str = field(default_factory=new_id)
    added_at: str = field(default_factory=now_iso)
    last_scan: str | None = None


@dataclass
class Document:
    source_id: str
    rel_path: str
    sha256: str
    mtime: float
    size: int
    owner: str = "local"
    title: str = ""
    mime: str = ""
    pages: int | None = None
    status: str = "ok"  # 'ok' | 'error: …' | 'skipped: …'
    id: str = field(default_factory=new_id)
    indexed_at: str = field(default_factory=now_iso)


@dataclass
class Chunk:
    doc_id: str
    ord: int
    text: str
    page: int | None = None
    heading: str = ""
    tokens: int = 0

    @property
    def id(self) -> str:
        return f"{self.doc_id}:{self.ord}"


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    text: str
    page: int | None
    heading: str
    path: str  # absolute path of the document
    score: float = 0.0
    keyword_rank: int | None = None
    vector_rank: int | None = None


@dataclass
class Filters:
    path_contains: str = ""
    extensions: tuple[str, ...] = ()
    owner: str = "local"


@dataclass
class StoreInfo:
    sources: int
    documents: int
    chunks: int
    vectors: int
    embed_model: str | None
    embed_dim: int | None
    schema_version: int
    size_bytes: int
