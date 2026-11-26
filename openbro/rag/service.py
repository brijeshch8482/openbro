"""DocsService — the only thing the agent, tools and CLI call.

Ingestion (change detection → extract → chunk → embed → store) and
hybrid search (BM25 + vectors merged with Reciprocal Rank Fusion) live
here, ABOVE the storage boundary, so every backend ranks the same way.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openbro.rag.chunker import chunk_pages
from openbro.rag.embedder import Embedder
from openbro.rag.extract import extract, indexable_extensions
from openbro.rag.models import Chunk, Document, Filters, Hit, Source, now_iso

RRF_K = 60
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "$RECYCLE.BIN"}

Progress = Callable[[str], None]


@dataclass
class RagConfig:
    chunk_tokens: int = 500
    chunk_overlap: int = 50
    top_k: int = 5
    candidates: int = 30
    max_file_mb: float = 200.0

    @classmethod
    def from_config(cls, config: dict | None) -> RagConfig:
        rag = (config or {}).get("rag", {}) or {}
        cfg = cls()
        for name in ("chunk_tokens", "chunk_overlap", "top_k", "candidates", "max_file_mb"):
            if name in rag:
                setattr(cfg, name, type(getattr(cfg, name))(rag[name]))
        return cfg


@dataclass
class ScanReport:
    source: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.added} new", f"{self.updated} updated", f"{self.unchanged} unchanged"]
        if self.removed:
            parts.append(f"{self.removed} removed")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        if self.errors:
            parts.append(f"{len(self.errors)} failed")
        return f"{self.source}: " + ", ".join(parts)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rrf_merge(lists: list[list[Hit]], k: int = RRF_K) -> list[Hit]:
    """RRF(chunk) = Σ 1 / (k + rank). Uses rank positions only, so BM25
    scores and cosine similarities never need a common scale."""
    merged: dict[str, Hit] = {}
    scores: dict[str, float] = {}
    for which, hits in enumerate(lists):
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            kept = merged.setdefault(hit.chunk_id, hit)
            if which == 0:
                kept.keyword_rank = rank
            else:
                kept.vector_rank = rank
    out = sorted(merged.values(), key=lambda h: scores[h.chunk_id], reverse=True)
    for h in out:
        h.score = round(scores[h.chunk_id], 5)
    return out


class DocsService:
    def __init__(
        self,
        store,
        blobs,
        embedder: Embedder | None = None,
        config: RagConfig | None = None,
        owner: str = "local",
    ):
        self.store = store
        self.blobs = blobs
        self.embedder = embedder
        self.config = config or RagConfig()
        self.owner = owner
        self._library = self.store.add_source(
            Source(kind="library", path=str(self.blobs.root), owner=owner)
        )

    # ─── sources ─────────────────────────────────────────────────────

    def add(self, path: str, progress: Progress | None = None) -> ScanReport:
        """Index a file or folder IN PLACE (nothing is copied)."""
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Not found: {p}")
        if self._is_inside_library(p):
            return self.scan(self._library, progress)
        source = self.store.add_source(
            Source(kind="folder" if p.is_dir() else "file", path=str(p), owner=self.owner)
        )
        return self.scan(source, progress)

    def upload(self, path: str, progress: Progress | None = None) -> ScanReport:
        """Copy a file into the library, then index the copy."""
        src = Path(path).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Not a file: {src}")
        self.blobs.put(src, src.name)
        return self.scan(self._library, progress)

    def sources(self) -> list[Source]:
        return self.store.list_sources(self.owner)

    def find_source(self, ref: str) -> Source | None:
        want = str(Path(ref).expanduser().resolve()) if ref else ""
        for s in self.sources():
            if s.id == ref or s.path == want or s.id.startswith(ref or "\0"):
                return s
        return None

    def remove(self, ref: str) -> str:
        """Stop using a source. Library uploads are deleted; in-place files
        are never touched."""
        source = self.find_source(ref)
        if source is not None and source.kind != "library":
            self.store.remove_source(source.id)
            return f"Removed {source.path} from the index (files untouched)."
        # A single uploaded document?
        p = Path(ref).expanduser().resolve()
        if self._is_inside_library(p):
            rel = p.relative_to(Path(self._library.path)).as_posix()
            doc = self.store.find_document(self._library.id, rel)
            if doc is not None:
                self.store.delete_document(doc.id)
            self.blobs.delete(p)
            return f"Deleted upload {p.name}."
        if source is not None and source.kind == "library":
            return "The library can't be removed; delete single uploads by path."
        return f"No indexed source matches {ref!r}. Use `docs list`."

    def _is_inside_library(self, p: Path) -> bool:
        lib = Path(self._library.path).resolve()
        return p == lib or lib in p.parents

    # ─── scanning ────────────────────────────────────────────────────

    def scan_all(self, progress: Progress | None = None) -> list[ScanReport]:
        return [self.scan(s, progress) for s in self.sources()]

    def _files(self, source: Source, report: ScanReport):
        root = Path(source.path)
        exts = indexable_extensions()
        if source.kind == "file":
            if root.is_file():
                yield root, root.name
            return
        if not root.is_dir():
            return
        for f in sorted(root.rglob("*")):
            if not f.is_file() or any(part in SKIP_DIRS for part in f.parts):
                continue
            if f.suffix.lower() not in exts:
                continue
            if f.stat().st_size > self.config.max_file_mb * 1024 * 1024:
                report.skipped.append(f"{f.name}: over {self.config.max_file_mb:g} MB")
                continue
            yield f, f.relative_to(root).as_posix()

    def scan(self, source: Source, progress: Progress | None = None) -> ScanReport:
        report = ScanReport(source=source.path)
        seen: set[str] = set()
        for f, rel in self._files(source, report):
            seen.add(rel)
            try:
                self._index_file(source, f, rel, report, progress)
            except Exception as e:
                report.errors.append(f"{rel}: {e}")
        for doc in self.store.list_documents(source.id):
            if doc.rel_path not in seen:
                self.store.delete_document(doc.id)
                report.removed += 1
        self.store.mark_scanned(source.id, now_iso())
        self._sync_embed_meta()
        return report

    def _index_file(self, source, f: Path, rel: str, report: ScanReport, progress) -> None:
        st = f.stat()
        existing = self.store.find_document(source.id, rel)
        if existing and existing.size == st.st_size and existing.mtime == st.st_mtime:
            report.unchanged += 1
            return
        digest = _sha256(f)
        if existing and existing.sha256 == digest:
            report.unchanged += 1
            return
        if progress:
            progress(f"indexing {rel}")
        try:
            extracted = extract(f)
        except ValueError as e:
            report.skipped.append(f"{rel}: {e}")
            doc = Document(
                source_id=source.id,
                rel_path=rel,
                sha256=digest,
                mtime=st.st_mtime,
                size=st.st_size,
                owner=self.owner,
                title=f.stem,
                mime=f.suffix.lower(),
                status=f"skipped: {e}",
            )
            self.store.upsert_document(doc, [])
            return
        pieces = chunk_pages(
            extracted.pages, self.config.chunk_tokens, self.config.chunk_overlap, extracted.kind
        )
        chunks = [
            Chunk(doc_id="", ord=i, text=p.text, page=p.page, heading=p.heading, tokens=p.tokens)
            for i, p in enumerate(pieces)
        ]
        vectors = self._embed([c.text for c in chunks]) if chunks else None
        pages = [n for n, _ in extracted.pages if n is not None]
        doc = Document(
            source_id=source.id,
            rel_path=rel,
            sha256=digest,
            mtime=st.st_mtime,
            size=st.st_size,
            owner=self.owner,
            title=extracted.title,
            mime=f.suffix.lower(),
            pages=max(pages) if pages else None,
        )
        self.store.upsert_document(doc, chunks, vectors)
        if existing:
            report.updated += 1
        else:
            report.added += 1

    # ─── embeddings ──────────────────────────────────────────────────

    def _embed(self, texts: list[str]) -> list[list[float]] | None:
        if self.embedder is None or not self._embed_model_matches():
            return None
        try:
            return self.embedder.embed_passages(texts)
        except Exception:
            self.embedder = None  # backend broken — keep indexing on keywords
            return None

    def _embed_model_matches(self) -> bool:
        stored = self.store.get_meta("embed_model")
        return stored in (None, "", self.embedder.model_id)

    def _sync_embed_meta(self) -> None:
        if self.embedder is None:
            return
        if not self.store.get_meta("embed_model") and self.store.info().vectors:
            self.store.set_meta("embed_model", self.embedder.model_id)
            self.store.set_meta("embed_dim", str(self.embedder.dim or ""))

    def reembed(self, progress: Progress | None = None) -> int:
        """Rebuild every vector with the current embedder (model changed)."""
        if self.embedder is None:
            return 0
        self.store.clear_vectors()
        self.store.set_meta("embed_model", self.embedder.model_id)
        done = 0
        while True:
            batch = self.store.chunks_without_vectors(256)
            if not batch:
                break
            vecs = self.embedder.embed_passages([c.text for c in batch])
            self.store.add_vectors(list(zip(batch, vecs)))
            done += len(batch)
            if progress:
                progress(f"embedded {done} chunks")
        self.store.set_meta("embed_dim", str(self.embedder.dim or ""))
        return done

    # ─── search / read ───────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: int | None = None,
        path_filter: str = "",
        type_filter: str = "",
    ) -> list[Hit]:
        k = k or self.config.top_k
        exts = tuple(
            e if e.startswith(".") else f".{e}"
            for e in (x.strip().lower() for x in type_filter.split(","))
            if e
        )
        filters = Filters(path_contains=path_filter, extensions=exts, owner=self.owner)
        n = max(self.config.candidates, k)
        lists = [self.store.keyword_search(query, n, filters)]
        if self.embedder is not None and self._embed_model_matches():
            try:
                lists.append(self.store.vector_search(self.embedder.embed_query(query), n, filters))
            except Exception:
                pass
        return rrf_merge(lists)[:k]

    def mode(self) -> str:
        if self.embedder is not None and self._embed_model_matches():
            return f"hybrid (BM25 + vectors, {self.embedder.model_id})"
        if self.embedder is not None:
            return "keyword only — embedding model changed, run `docs reindex`"
        return "keyword only (BM25) — install `openbro[rag]` for meaning-based search"

    def resolve_document(self, ref: str) -> tuple[Document, str] | None:
        """(document, absolute path) for a doc id or a file path."""
        doc = self.store.get_document(ref)
        if doc is not None:
            return doc, self._abs(doc)
        want = str(Path(ref).expanduser().resolve())
        for s in self.sources():
            root = Path(s.path)
            if s.kind == "file" and s.path == want:
                d = self.store.find_document(s.id, root.name)
            elif s.kind != "file" and (root == Path(want) or root in Path(want).parents):
                d = self.store.find_document(s.id, Path(want).relative_to(root).as_posix())
            else:
                continue
            if d is not None:
                return d, want
        return None

    def _abs(self, doc: Document) -> str:
        for s in self.sources():
            if s.id == doc.source_id:
                return s.path if s.kind == "file" else str(Path(s.path) / doc.rel_path)
        return doc.rel_path

    def read(self, ref: str, page: int | None = None) -> str:
        found = self.resolve_document(ref)
        if found is None:
            return f"Not in the index: {ref}. Use `docs search` or `docs list`."
        doc, path = found
        chunks = self.store.read_chunks(doc.id, page)
        if not chunks:
            return f"No text for {path}" + (f" page {page}" if page else "") + f" ({doc.status})"
        text = _join_chunks([c.text for c in chunks], self.config.chunk_overlap * 4)
        head = f"{path}" + (f" — page {page}" if page else "")
        return f"{head}\n\n{text}"

    # ─── portability ─────────────────────────────────────────────────

    def export(self, bundle: Path) -> int:
        """JSONL bundle of every row (+ library files alongside)."""
        bundle = Path(bundle)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with open(bundle, "w", encoding="utf-8") as f:
            for table, row in self.store.dump():
                f.write(json.dumps({"table": table, "row": row}, ensure_ascii=False) + "\n")
                n += 1
        lib_out = bundle.with_suffix(".library")
        if any(Path(self._library.path).iterdir()):
            shutil.copytree(self._library.path, lib_out, dirs_exist_ok=True)
        return n

    def import_bundle(self, bundle: Path) -> int:
        bundle = Path(bundle)
        rows = [
            json.loads(line) for line in bundle.read_text(encoding="utf-8").splitlines() if line
        ]
        incoming_model = next(
            (
                r["row"]["value"]
                for r in rows
                if r["table"] == "meta" and r["row"]["key"] == "embed_model"
            ),
            None,
        )
        keep_vectors = (
            self.embedder is not None and incoming_model == self.embedder.model_id
        ) or self.embedder is None
        lib_path = self._library.path
        # The other machine's library becomes this machine's library.
        foreign_libs = {
            r["row"]["id"]
            for r in rows
            if r["table"] == "sources" and r["row"].get("kind") == "library"
        }
        n = 0
        for r in rows:
            table, row = r["table"], dict(r["row"])
            if table == "chunk_vectors" and not keep_vectors:
                continue
            if table == "meta" and row["key"] in ("schema_version", "embed_model", "embed_dim"):
                if not keep_vectors or row["key"] == "schema_version":
                    continue
            if table == "sources" and row["id"] in foreign_libs:
                continue  # merged into the local library source
            if table == "documents" and row.get("source_id") in foreign_libs:
                row["source_id"] = self._library.id
            self.store.load_rows(table, row)
            n += 1
        lib_in = bundle.with_suffix(".library")
        if lib_in.is_dir():
            shutil.copytree(lib_in, lib_path, dirs_exist_ok=True)
        if not keep_vectors:
            self.reembed()
        return n

    def status(self) -> dict:
        info = self.store.info()
        return {
            "mode": self.mode(),
            "sources": info.sources,
            "documents": info.documents,
            "chunks": info.chunks,
            "vectors": info.vectors,
            "embed_model": info.embed_model,
            "index_path": str(self.store.path),
            "index_mb": round(info.size_bytes / 1_048_576, 2),
            "library": self._library.path,
        }


def _join_chunks(texts: list[str], overlap_chars: int) -> str:
    """Re-join chunk texts, dropping the overlap repeated at each start."""
    out = texts[0] if texts else ""
    for t in texts[1:]:
        probe = t[: max(overlap_chars, 1)]
        cut = 0
        for size in range(min(len(probe), len(out)), 20, -1):
            if out.endswith(t[:size]):
                cut = size
                break
        out += ("\n\n" if not cut else "") + t[cut:]
    return out
