"""Build the JSONL training dataset from raw public documents.

Each entry in `training.jsonl` follows the simple instruction-tuning
shape that `peft` + `transformers` expect:

    {"input": "<user prompt>", "output": "<ideal model response>"}

The transformations here are deterministic — no LLM in the loop, no
"smart" pre-processing of the model's job. Tools do one thing; this
file's job is to clean and reshape, not to teach the model how to
think.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbro.training.data_sources import RawDoc


@dataclass
class TrainingExample:
    """A single (prompt, response) pair ready for LoRA fine-tuning."""

    prompt: str
    response: str
    source: str
    source_id: str
    source_url: str

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "input": self.prompt,
                "output": self.response,
                "_source": self.source,
                "_id": self.source_id,
                "_url": self.source_url,
            },
            ensure_ascii=False,
        )


# ─── Text cleaning ──────────────────────────────────────────────────


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """Remove HTML tags + collapse whitespace. Stack Overflow and
    GitHub bodies contain HTML; arXiv and Reddit are plain."""
    if not text:
        return ""
    out = _HTML_TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", out).strip()


def normalise_doc(doc: RawDoc) -> RawDoc:
    """Strip markup from title + body, return a fresh RawDoc.

    Pure transformation — does not mutate the input.
    """
    return RawDoc(
        source=doc.source,
        id=doc.id,
        title=strip_html(doc.title),
        body=strip_html(doc.body),
        url=doc.url,
        tags=doc.tags,
        score=doc.score,
        fetched_at=doc.fetched_at,
    )


# ─── Prompt / response builders ─────────────────────────────────────


def to_examples(doc: RawDoc) -> list[TrainingExample]:
    """Build TrainingExample objects from a single RawDoc.

    The shape depends on the source — Stack Overflow gives one
    question / one answer pair, Wikipedia gives a "summarise this"
    instruction, etc. Per-source builders are pure functions of the
    document — no special-casing for what's "useful" content.
    """
    if doc.source == "stackoverflow":
        return [_so_to_example(doc)]
    if doc.source == "github":
        return [_github_to_example(doc)]
    if doc.source == "wikipedia":
        return [_wiki_to_example(doc)]
    if doc.source == "reddit":
        return [_reddit_to_example(doc)]
    if doc.source == "arxiv":
        return [_arxiv_to_example(doc)]
    return []


def _so_to_example(doc: RawDoc) -> TrainingExample:
    return TrainingExample(
        prompt=doc.title,
        response=doc.body[:2000],
        source=doc.source,
        source_id=doc.id,
        source_url=doc.url,
    )


def _github_to_example(doc: RawDoc) -> TrainingExample:
    prompt = doc.title
    if doc.body:
        prompt = f"{doc.title}\n\n{doc.body[:1500]}"
    return TrainingExample(
        prompt=prompt[:2000],
        response=doc.body[:2000],
        source=doc.source,
        source_id=doc.id,
        source_url=doc.url,
    )


def _wiki_to_example(doc: RawDoc) -> TrainingExample:
    return TrainingExample(
        prompt=f"What is {doc.title}?",
        response=doc.body[:2000],
        source=doc.source,
        source_id=doc.id,
        source_url=doc.url,
    )


def _reddit_to_example(doc: RawDoc) -> TrainingExample:
    return TrainingExample(
        prompt=doc.title,
        response=doc.body[:2000] if doc.body else doc.title,
        source=doc.source,
        source_id=doc.id,
        source_url=doc.url,
    )


def _arxiv_to_example(doc: RawDoc) -> TrainingExample:
    return TrainingExample(
        prompt=f"Summarise: {doc.title}",
        response=doc.body[:2000],
        source=doc.source,
        source_id=doc.id,
        source_url=doc.url,
    )


# ─── Dataset filters ────────────────────────────────────────────────


def dedupe_by_text(examples: list[TrainingExample]) -> list[TrainingExample]:
    """Drop duplicate (prompt, response) pairs by content hash."""
    seen: set[str] = set()
    out: list[TrainingExample] = []
    for ex in examples:
        key = hashlib.sha1(f"{ex.prompt}::{ex.response}".encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(ex)
    return out


def length_filter(
    examples: list[TrainingExample],
    min_prompt: int = 10,
    min_response: int = 30,
    max_total: int = 4000,
) -> list[TrainingExample]:
    """Drop examples that are too short to be useful or too long to
    fit comfortably in the base model's context window."""
    out: list[TrainingExample] = []
    for ex in examples:
        if len(ex.prompt) < min_prompt:
            continue
        if len(ex.response) < min_response:
            continue
        if len(ex.prompt) + len(ex.response) > max_total:
            continue
        out.append(ex)
    return out


# ─── Build pipeline ─────────────────────────────────────────────────


def build(
    docs: Iterable[RawDoc],
    out_path: Path,
    min_prompt: int = 10,
    min_response: int = 30,
    max_total: int = 4000,
) -> dict[str, Any]:
    """Run the full dataset build pipeline:

    1. Normalise each RawDoc (strip HTML / collapse whitespace).
    2. Convert to TrainingExample(s).
    3. Dedupe by (prompt, response) hash.
    4. Length filter.
    5. Write JSONL to `out_path`.
    6. Return a metadata dict (counts, hashes, source breakdown).

    Returns the metadata so callers can serialise it alongside the
    JSONL for run-reproducibility.
    """
    examples: list[TrainingExample] = []
    for doc in docs:
        norm = normalise_doc(doc)
        examples.extend(to_examples(norm))

    pre_dedupe = len(examples)
    examples = dedupe_by_text(examples)
    post_dedupe = len(examples)
    examples = length_filter(examples, min_prompt, min_response, max_total)
    final_count = len(examples)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with out_path.open("w", encoding="utf-8") as f:
        for ex in examples:
            line = ex.to_jsonl()
            f.write(line + "\n")
            digest.update(line.encode("utf-8"))

    # Breakdown of where examples came from — useful for the training
    # run's release notes.
    by_source: dict[str, int] = {}
    for ex in examples:
        by_source[ex.source] = by_source.get(ex.source, 0) + 1

    return {
        "pre_dedupe": pre_dedupe,
        "post_dedupe": post_dedupe,
        "final_count": final_count,
        "by_source": by_source,
        "dataset_sha256": digest.hexdigest(),
        "out_path": str(out_path),
    }
