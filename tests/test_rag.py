"""Harness piece 5 — document RAG: chunking, indexing, hybrid search,
read, sources, uploads, model changes, export/import, docs tool."""

import math
import os
import time

import pytest

from openbro.memory.ranking import tokenize
from openbro.rag import build_docs_service
from openbro.rag.chunker import chunk_pages
from openbro.rag.extract import extract
from openbro.rag.local_blobs import LibraryBlobStore
from openbro.rag.local_store import SQLiteDocStore
from openbro.rag.models import Hit
from openbro.rag.service import DocsService, RagConfig, rrf_merge
from openbro.tools.docs_tool import DocsTool

# Words in the same group mean the same thing to the fake embedder.
CONCEPTS = [
    {"fee", "fees", "payment", "paid"},
    {"receipt", "acknowledgement", "invoice"},
    {"battery", "power"},
    {"holiday", "vacation", "leave"},
]


class FakeEmbedder:
    """Deterministic 'meaning' vectors: one dimension per concept group."""

    def __init__(self, model_id="fake-v1"):
        self.model_id = model_id
        self.dim = len(CONCEPTS) + 1

    def _vec(self, text):
        v = [0.0] * self.dim
        for tok in tokenize(text):
            for i, group in enumerate(CONCEPTS):
                if tok in group:
                    v[i] += 1.0
        v[-1] = 0.01
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


def _service(tmp_path, embedder=None, **cfg):
    return DocsService(
        store=SQLiteDocStore(tmp_path / "idx" / "index.db"),
        blobs=LibraryBlobStore(tmp_path / "idx" / "library"),
        embedder=embedder,
        config=RagConfig(**cfg),
    )


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Documents"
    (root / "fees").mkdir(parents=True)
    (root / "fees" / "march.txt").write_text(
        "Payment acknowledgement\nAmount paid: Rs 45,000 for the semester.\n", encoding="utf-8"
    )
    (root / "fees" / "invoice.md").write_text(
        "# Invoice INV-2041\n\nLaptop repair, total 3,200.\n", encoding="utf-8"
    )
    (root / "notes.md").write_text(
        "# Trip plan\n\nHoliday in Manali during December.\n", encoding="utf-8"
    )
    (root / "marks.csv").write_text("name,marks\nasha,91\nravi,78\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "skip.txt").write_text("invoice", encoding="utf-8")
    return root


# ─── chunker ──────────────────────────────────────────────────────────


def test_chunks_keep_headings_pages_and_size():
    text = "# Intro\n\n" + ("alpha beta gamma. " * 200) + "\n\n# Second\n\nshort part"
    pieces = chunk_pages([(3, text)], chunk_tokens=100, overlap_tokens=10)
    assert all(len(p.text) <= 400 for p in pieces)
    assert pieces[0].heading == "Intro" and pieces[-1].heading == "Second"
    assert all(p.page == 3 for p in pieces)
    # overlap: each later chunk of the long section starts with the tail of the previous
    intro = [p for p in pieces if p.heading == "Intro"]
    assert len(intro) > 2
    assert intro[1].text[:20] in intro[0].text


def test_table_chunks_repeat_header():
    rows = "\n".join(f"row{i},{i}" for i in range(400))
    pieces = chunk_pages([(None, "name,value\n" + rows)], chunk_tokens=100, kind="table")
    assert len(pieces) > 1
    assert all(p.text.startswith("name,value\n") for p in pieces)


def test_rrf_prefers_chunks_in_both_lists():
    def h(cid):
        return Hit(chunk_id=cid, doc_id="d", text=cid, page=None, heading="", path="p")

    merged = rrf_merge([[h("a"), h("b"), h("c")], [h("c"), h("d")]])
    assert [x.chunk_id for x in merged][:2] == ["c", "a"]
    assert merged[0].keyword_rank == 3 and merged[0].vector_rank == 1


# ─── extract ──────────────────────────────────────────────────────────


def test_extract_text_and_rejects_empty(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("hello", encoding="utf-8")
    assert extract(f).pages[0][1] == "hello"
    empty = tmp_path / "b.txt"
    empty.write_text("   ", encoding="utf-8")
    with pytest.raises(ValueError):
        extract(empty)


def test_extract_pdf_pages(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    path = tmp_path / "blank.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    with pytest.raises(ValueError):  # blank page → no text → DocumentTool also finds none
        extract(path)


# ─── indexing ─────────────────────────────────────────────────────────


def test_add_folder_indexes_supported_files(tmp_path, library):
    svc = _service(tmp_path)
    report = svc.add(str(library))
    assert report.added == 4 and not report.errors
    st = svc.status()
    assert st["documents"] == 4 and st["chunks"] >= 4
    rels = {d.rel_path for s in svc.sources() for d in svc.store.list_documents(s.id)}
    assert "fees/march.txt" in rels and not any("node_modules" in r for r in rels)


def test_rescan_is_incremental(tmp_path, library):
    svc = _service(tmp_path)
    svc.add(str(library))
    again = svc.add(str(library))
    assert (again.added, again.updated, again.unchanged) == (0, 0, 4)

    target = library / "notes.md"
    target.write_text("# Trip plan\n\nVacation moved to January.\n", encoding="utf-8")
    os.utime(target, (time.time() + 30, time.time() + 30))
    (library / "marks.csv").unlink()
    third = svc.add(str(library))
    assert third.updated == 1 and third.removed == 1
    assert "January" in svc.read(str(target))


def test_touch_without_content_change_is_unchanged(tmp_path, library):
    svc = _service(tmp_path)
    svc.add(str(library))
    f = library / "notes.md"
    os.utime(f, (time.time() + 60, time.time() + 60))
    assert svc.add(str(library)).unchanged == 4


def test_big_files_skipped(tmp_path, library):
    (library / "huge.txt").write_text("x" * 3_000_000, encoding="utf-8")
    svc = _service(tmp_path, max_file_mb=1)
    report = svc.add(str(library))
    assert any("huge.txt" in s for s in report.skipped)


# ─── search ───────────────────────────────────────────────────────────


def test_keyword_search_finds_exact_tokens(tmp_path, library):
    svc = _service(tmp_path)
    svc.add(str(library))
    hits = svc.search("INV-2041")
    assert hits and hits[0].path.endswith("invoice.md")
    assert "keyword only" in svc.mode()


def test_hybrid_search_finds_meaning_matches(tmp_path, library):
    svc = _service(tmp_path, embedder=FakeEmbedder())
    svc.add(str(library))
    hits = svc.search("fee receipt")  # no chunk contains both words literally
    assert hits[0].path.endswith("march.txt")
    assert hits[0].vector_rank == 1
    assert "hybrid" in svc.mode()


def test_search_filters(tmp_path, library):
    svc = _service(tmp_path, embedder=FakeEmbedder())
    svc.add(str(library))
    assert all(h.path.endswith(".md") for h in svc.search("invoice holiday", type_filter="md"))
    assert all("fees" in h.path for h in svc.search("invoice holiday", path_filter="fees"))


def test_read_by_path_and_id(tmp_path, library):
    svc = _service(tmp_path)
    svc.add(str(library))
    hit = svc.search("Manali")[0]
    assert "Manali" in svc.read(hit.doc_id)
    assert "Manali" in svc.read(str(library / "notes.md"))
    assert "Not in the index" in svc.read(str(library / "nope.md"))


# ─── sources, uploads ─────────────────────────────────────────────────


def test_remove_source_keeps_files(tmp_path, library):
    svc = _service(tmp_path)
    svc.add(str(library))
    assert "files untouched" in svc.remove(str(library))
    assert (library / "notes.md").exists()
    assert svc.status()["documents"] == 0


def test_upload_copies_into_library_and_delete(tmp_path, library):
    svc = _service(tmp_path)
    report = svc.upload(str(library / "notes.md"))
    assert report.added == 1
    copy = tmp_path / "idx" / "library" / "notes.md"
    assert copy.exists() and (library / "notes.md").exists()
    svc.upload(str(library / "notes.md"))
    assert (tmp_path / "idx" / "library" / "notes (2).md").exists()
    assert "Deleted upload" in svc.remove(str(copy))
    assert not copy.exists()


def test_single_file_source(tmp_path, library):
    svc = _service(tmp_path)
    assert svc.add(str(library / "fees" / "invoice.md")).added == 1
    assert svc.search("laptop repair")[0].path.endswith("invoice.md")


# ─── embedding model changes ──────────────────────────────────────────


def test_model_change_falls_back_to_keywords_until_reembed(tmp_path, library):
    _service(tmp_path, embedder=FakeEmbedder("fake-v1")).add(str(library))
    svc = _service(tmp_path, embedder=FakeEmbedder("fake-v2"))
    assert "reindex" in svc.mode()
    assert svc.search("INV-2041")  # keyword path still works
    n = svc.reembed()
    assert n == svc.status()["chunks"]
    assert "hybrid" in svc.mode() and svc.status()["embed_model"] == "fake-v2"


# ─── portability ──────────────────────────────────────────────────────


def test_export_import_roundtrip(tmp_path, library):
    src = _service(tmp_path / "a", embedder=FakeEmbedder())
    src.add(str(library))
    src.upload(str(library / "notes.md"))
    bundle = tmp_path / "bundle.jsonl"
    rows = src.export(bundle)
    assert rows > 0

    dst = _service(tmp_path / "b", embedder=FakeEmbedder())
    dst.import_bundle(bundle)
    assert dst.status()["documents"] == src.status()["documents"]
    assert dst.status()["vectors"] == src.status()["vectors"]
    assert dst.search("fee receipt")[0].path.endswith("march.txt")
    assert (tmp_path / "b" / "idx" / "library" / "notes.md").exists()


def test_import_with_other_model_reembeds(tmp_path, library):
    src = _service(tmp_path / "a", embedder=FakeEmbedder("fake-v1"))
    src.add(str(library))
    bundle = tmp_path / "bundle.jsonl"
    src.export(bundle)
    dst = _service(tmp_path / "b", embedder=FakeEmbedder("fake-v2"))
    dst.import_bundle(bundle)
    assert dst.status()["embed_model"] == "fake-v2"
    assert dst.status()["vectors"] == dst.status()["chunks"]


def test_remote_backend_not_available_yet():
    with pytest.raises(NotImplementedError):
        build_docs_service({"storage": {"backend": "remote"}})


# ─── docs tool ────────────────────────────────────────────────────────


def test_docs_tool_actions(tmp_path, library):
    tool = DocsTool(_service(tmp_path, embedder=FakeEmbedder()))
    assert "Nothing is indexed yet" in tool.run(action="search", query="fee")
    assert "Indexed" in tool.run(action="add", path=str(library))
    out = tool.run(action="search", query="fee receipt")
    assert "march.txt" in out and "doc=" in out
    doc_id = out.split("doc=")[1].split(",")[0]
    assert "45,000" in tool.run(action="read", doc=doc_id)
    assert "4 documents" in tool.run(action="list")
    assert "files untouched" in tool.run(action="remove", path=str(library))
    assert "Unknown action" in tool.run(action="explode")


def test_docs_tool_risk_by_action():
    from openbro.tools.base import RiskLevel

    tool = DocsTool(service=object())
    assert tool.compute_risk({"action": "search"}) == RiskLevel.SAFE
    assert tool.compute_risk({"action": "add"}) == RiskLevel.MODERATE
    assert tool.compute_risk({"action": "remove"}) == RiskLevel.MODERATE
