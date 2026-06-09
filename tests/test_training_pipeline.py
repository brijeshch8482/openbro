"""Tests for the training-pipeline plumbing.

Only the pure-Python data wrangling is tested here. The heavy ML
parts (LoRA fine-tune, GGUF conversion, HuggingFace upload) are
gated behind environment availability and not exercised in CI.
"""

from __future__ import annotations

import json

from openbro.training.data_sources import RawDoc
from openbro.training.dataset import (
    TrainingExample,
    build,
    dedupe_by_text,
    length_filter,
    normalise_doc,
    strip_html,
    to_examples,
)
from openbro.training.status import RunInfo, days_since_last, load_runs

# ─── Helpers ─────────────────────────────────────────────────────────


def _make_doc(
    source: str = "stackoverflow",
    id: str = "1",
    title: str = "How do I do X?",
    body: str = "You do X by running Y. Step-by-step instructions follow.",
    url: str = "https://example.com/1",
    tags: list[str] | None = None,
) -> RawDoc:
    return RawDoc(
        source=source,
        id=id,
        title=title,
        body=body,
        url=url,
        tags=tags or [],
    )


# ─── strip_html ──────────────────────────────────────────────────────


def test_strip_html_removes_tags():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_collapses_whitespace():
    assert strip_html("   foo\n\n\n   bar  \t baz  ") == "foo bar baz"


def test_strip_html_empty():
    assert strip_html("") == ""
    assert strip_html(None) == ""  # type: ignore[arg-type]


# ─── normalise_doc ──────────────────────────────────────────────────


def test_normalise_doc_strips_body_html():
    doc = _make_doc(body="<p>Hello <strong>world</strong></p>")
    normalised = normalise_doc(doc)
    assert normalised.body == "Hello world"
    assert normalised.title == "How do I do X?"


def test_normalise_doc_does_not_mutate_input():
    doc = _make_doc(body="<i>raw</i>")
    _ = normalise_doc(doc)
    assert doc.body == "<i>raw</i>"  # untouched


# ─── to_examples ────────────────────────────────────────────────────


def test_to_examples_stackoverflow():
    doc = _make_doc(source="stackoverflow", title="Q?", body="A.")
    exs = to_examples(doc)
    assert len(exs) == 1
    assert exs[0].prompt == "Q?"
    assert exs[0].response == "A."
    assert exs[0].source == "stackoverflow"


def test_to_examples_wikipedia_prefixes_what_is():
    doc = _make_doc(source="wikipedia", title="Pandas", body="Pandas is a library...")
    exs = to_examples(doc)
    assert len(exs) == 1
    assert exs[0].prompt.startswith("What is Pandas")


def test_to_examples_arxiv_prefixes_summarise():
    doc = _make_doc(source="arxiv", title="LoRA paper", body="LoRA introduces low-rank adapters...")
    exs = to_examples(doc)
    assert exs[0].prompt.startswith("Summarise:")


def test_to_examples_unknown_source_returns_empty():
    doc = _make_doc(source="unknown")
    assert to_examples(doc) == []


# ─── dedupe_by_text ─────────────────────────────────────────────────


def test_dedupe_drops_exact_duplicates():
    a = TrainingExample("Q", "A", "x", "1", "u")
    b = TrainingExample("Q", "A", "y", "2", "v")  # different metadata, same content
    c = TrainingExample("Q2", "A", "z", "3", "w")
    out = dedupe_by_text([a, b, c])
    assert len(out) == 2
    assert out[0] is a
    assert out[1] is c


def test_dedupe_preserves_order():
    a = TrainingExample("First", "ans", "s", "1", "u")
    b = TrainingExample("Second", "ans", "s", "2", "u")
    c = TrainingExample("Third", "ans", "s", "3", "u")
    out = dedupe_by_text([a, b, c])
    assert [e.prompt for e in out] == ["First", "Second", "Third"]


# ─── length_filter ──────────────────────────────────────────────────


def test_length_filter_drops_short_prompts():
    short = TrainingExample("hi", "a" * 100, "s", "1", "u")
    ok = TrainingExample("hello world how are you", "a" * 100, "s", "2", "u")
    out = length_filter([short, ok])
    assert out == [ok]


def test_length_filter_drops_long_total():
    long_total = TrainingExample("Q" * 2500, "A" * 2500, "s", "1", "u")
    ok = TrainingExample("How does this work", "A" * 100, "s", "2", "u")
    out = length_filter([long_total, ok], max_total=4000)
    assert out == [ok]


# ─── build (end-to-end) ─────────────────────────────────────────────


def test_build_writes_jsonl_and_returns_metadata(tmp_path):
    docs = [
        _make_doc(
            id="1",
            title="How do I read an xlsx file with pandas?",
            body="You read xlsx files using pd.read_excel(path)." * 3,
        ),
        _make_doc(
            id="2",
            title="What is the best way to call shell from Python?",
            body="Use subprocess.run with check=True for safety." * 3,
        ),
        _make_doc(
            id="3",
            source="wikipedia",
            title="Pandas software",
            body="Pandas is a Python data analysis library used for data manipulation." * 3,
        ),
    ]
    out_path = tmp_path / "training.jsonl"
    meta = build(docs, out_path)

    assert out_path.exists()
    assert meta["final_count"] == 3
    assert meta["by_source"]["stackoverflow"] == 2
    assert meta["by_source"]["wikipedia"] == 1
    assert "dataset_sha256" in meta

    # Each line is a JSON object with input/output.
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert all("input" in p and "output" in p for p in parsed)


def test_build_deduplicates(tmp_path):
    docs = [
        _make_doc(
            id="1",
            title="Same question asked multiple times",
            body="Same answer body content here." * 3,
        ),
        _make_doc(
            id="2",
            title="Same question asked multiple times",
            body="Same answer body content here." * 3,
        ),
    ]
    out_path = tmp_path / "training.jsonl"
    meta = build(docs, out_path)
    assert meta["pre_dedupe"] == 2
    assert meta["final_count"] == 1


# ─── Status: load_runs + days_since_last ────────────────────────────


def test_load_runs_returns_empty_when_no_dir(tmp_path):
    assert load_runs(tmp_path) == []


def test_load_runs_parses_summaries(tmp_path):
    runs_dir = tmp_path / "training_runs"
    run1 = runs_dir / "20260601-100000"
    run1.mkdir(parents=True)
    (run1 / "summary.json").write_text(
        json.dumps(
            {
                "run_id": "20260601-100000",
                "started": 1717248000.0,
                "finished": 1717266000.0,
                "dataset": {"final_count": 1500},
                "finetune": {"train_loss": 1.42},
                "convert": {"size_mb": 712.3},
                "publish": {
                    "pr": {"url": "https://github.com/x/y/pull/3"},
                    "hf": {"repo": "x/y"},
                },
            }
        )
    )
    runs = load_runs(tmp_path)
    assert len(runs) == 1
    r = runs[0]
    assert r.run_id == "20260601-100000"
    assert r.dataset_count == 1500
    assert r.train_loss == 1.42
    assert r.gguf_size_mb == 712.3
    assert r.pr_url == "https://github.com/x/y/pull/3"
    assert r.hf_uploaded is True


def test_load_runs_handles_partial_summaries(tmp_path):
    """Failed runs save a partial summary — should still parse."""
    runs_dir = tmp_path / "training_runs"
    run1 = runs_dir / "20260602-100000"
    run1.mkdir(parents=True)
    (run1 / "summary.json").write_text(
        json.dumps({"run_id": "20260602-100000", "started": 1717334400.0})
    )
    runs = load_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].run_id == "20260602-100000"
    assert runs[0].train_loss is None
    assert runs[0].pr_url == ""
    assert runs[0].hf_uploaded is False


def test_days_since_last_uses_finished_when_present():
    r = RunInfo(
        run_id="x",
        started=0.0,
        finished=0.0,
        dataset_count=0,
        train_loss=None,
        gguf_size_mb=None,
        pr_url=None,
        hf_uploaded=False,
    )

    # finished=0 means epoch — should be many days ago
    assert days_since_last(r) > 1000


# ─── CLI shape ──────────────────────────────────────────────────────


def test_train_group_has_three_subcommands():
    """`openbro train` exposes setup, status, run."""
    from openbro.training.cli import train

    assert sorted(train.commands.keys()) == ["run", "setup", "status"]


def test_model_group_has_update_and_info():
    """`openbro model` exposes update + info alongside the existing commands."""
    from openbro.cli.main import model

    cmds = set(model.commands.keys())
    assert "update" in cmds
    assert "info" in cmds


def test_openbro_1b_in_catalog():
    """The catalog includes openbro:1b so `openbro model download openbro:1b` works."""
    from openbro.utils.local_llm_setup import MODELS

    assert "openbro:1b" in MODELS
    info = MODELS["openbro:1b"]
    assert info["repo"] == "brijeshch8482/openbro-1b-instruct"
    assert info["file"] == "openbro.gguf"
