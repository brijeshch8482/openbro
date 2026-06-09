"""`openbro train` CLI integration.

Manually triggered by the maintainer. Runs the full pipeline:

  fetch_all -> dataset.build -> finetune.run -> to_gguf.convert
              -> validate.smoke_test -> publish.publish

Each stage logs progress to stdout + writes its summary JSON to the
run directory. A `summary.json` aggregates everything at the end so
release notes can be assembled in one place.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import click

from openbro.training import (
    DEFAULT_BASE_MODEL,
    DEFAULT_OUTPUT_NAME,
    PIPELINE_VERSION,
)

# Default source config — small numbers so the first run completes
# quickly. Bump these in the config file once the pipeline is verified.
_DEFAULT_SOURCES = {
    "stackoverflow": {
        "tags": ["python", "powershell", "bash", "pandas", "openpyxl", "subprocess"],
        "per_tag": 50,
    },
    "github": {
        "repos": [
            "brijeshch8482/openbro",
            "ggerganov/llama.cpp",
            "huggingface/transformers",
        ],
        "per_repo": 30,
    },
    "wikipedia": {
        "titles": [
            "Pandas (software)",
            "GGUF",
            "Large language model",
            "Quantization (signal processing)",
        ]
    },
    "reddit": {
        "subreddits": ["LocalLLaMA", "MachineLearning", "LearnPython"],
        "per_sub": 25,
        "sort": "top",
        "timeframe": "month",
    },
    "arxiv": {
        "queries": ["LoRA fine-tuning", "instruction tuning", "small language models"],
        "per_query": 15,
    },
}


_DEFAULT_ROOT = "D:/OpenBro-teting"


@click.group("train", invoke_without_command=True)
@click.pass_context
def train(ctx: click.Context) -> None:
    """Train and publish openbro.gguf.

    With no subcommand, runs the full pipeline (equivalent to
    `openbro train run`). Subcommands: `setup` for pre-flight checks
    and `status` for past-run history.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(run_pipeline)


@train.command("setup")
@click.option(
    "--root",
    type=click.Path(file_okay=False),
    default=_DEFAULT_ROOT,
    help="Root dir holding models/, training_queue/, openbro-model/.",
)
def setup_cmd(root: str) -> None:
    """Pre-flight checks: ML deps, CUDA, HF login, gh CLI, llama.cpp,
    model repo clone, base-model access."""
    from openbro.training.setup_helper import run_all_checks

    results = run_all_checks(Path(root))
    ok_count = sum(1 for r in results if r.ok)
    click.echo(f"\nPre-flight checks ({ok_count}/{len(results)} passing):\n")
    for r in results:
        glyph = "✓" if r.ok else "✗"
        color = "green" if r.ok else "red"
        click.secho(f"  {glyph} {r.name:<25} {r.detail}", fg=color)
        if not r.ok and r.fix_hint:
            for line in r.fix_hint.splitlines():
                click.echo(f"        {line}")
    if ok_count == len(results):
        click.echo("\nAll checks passed. Ready to run: openbro train")
    else:
        click.echo(
            f"\n{len(results) - ok_count} blocker(s). Fix the marked items, then re-run setup."
        )


@train.command("status")
@click.option(
    "--root",
    type=click.Path(file_okay=False),
    default=_DEFAULT_ROOT,
    help="Root dir holding training_runs/.",
)
def status_cmd(root: str) -> None:
    """Show past training-run summaries."""
    from openbro.training.status import days_since_last, load_runs

    runs = load_runs(Path(root))
    if not runs:
        click.echo("No training runs yet. Trigger one with `openbro train`.")
        return
    click.echo(f"Past runs ({len(runs)} total, oldest-first):\n")
    for r in runs:
        click.echo(f"  {r.run_id}")
        click.echo(f"    dataset:  {r.dataset_count} examples")
        if r.train_loss is not None:
            click.echo(f"    loss:     {r.train_loss:.3f}")
        if r.gguf_size_mb is not None:
            click.echo(f"    gguf:     {r.gguf_size_mb} MB")
        if r.pr_url:
            click.echo(f"    PR:       {r.pr_url}")
        if r.hf_uploaded:
            click.echo("    HF:       uploaded")
        click.echo()
    last = runs[-1]
    days = days_since_last(last)
    click.echo(f"Last run: {last.run_id} — {days:.1f} day(s) ago")


@train.command("run")
@click.option("--no-publish", is_flag=True, help="Skip Stage 6 (no PR, no HF push).")
@click.option(
    "--skip-fetch",
    is_flag=True,
    help="Reuse the most recent training_queue instead of fetching fresh data.",
)
@click.option("--quick", is_flag=True, help="1 epoch instead of 3 — debug only.")
@click.option(
    "--base-model",
    default=DEFAULT_BASE_MODEL,
    help="HuggingFace base model id.",
)
@click.option(
    "--root",
    type=click.Path(file_okay=False),
    default=_DEFAULT_ROOT,
    help="Root dir holding models/, training_queue/, training_runs/.",
)
def run_pipeline(
    no_publish: bool, skip_fetch: bool, quick: bool, base_model: str, root: str
) -> None:
    """Run the full openbro.gguf training pipeline end-to-end."""
    root_p = Path(root)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    queue_dir = root_p / "training_queue" / run_id
    runs_dir = root_p / "training_runs" / run_id
    models_dir = root_p / "models"
    queue_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, object] = {
        "run_id": run_id,
        "pipeline_version": PIPELINE_VERSION,
        "started": time.time(),
        "base_model": base_model,
    }

    # ─── Stage 1: Fetch ─────────────────────────────────────────
    if not skip_fetch:
        click.echo(f"[1/6] Fetching public data → {queue_dir}")
        from openbro.training import data_sources

        docs = data_sources.fetch_all(_DEFAULT_SOURCES)
        click.echo(f"      Fetched {len(docs)} raw documents")
        # Persist raw fetch so re-runs can use --skip-fetch.
        raw_path = queue_dir / "raw.jsonl"
        with raw_path.open("w", encoding="utf-8") as f:
            for d in docs:
                f.write(
                    json.dumps(
                        {
                            "source": d.source,
                            "id": d.id,
                            "title": d.title,
                            "body": d.body,
                            "url": d.url,
                            "tags": d.tags,
                            "score": d.score,
                        }
                    )
                    + "\n"
                )
        summary["fetch"] = {"count": len(docs), "raw_path": str(raw_path)}
    else:
        # Pick the most recent queue dir as input.
        existing = sorted((root_p / "training_queue").glob("*/raw.jsonl"))
        if not existing:
            raise click.ClickException("--skip-fetch requested but no existing raw.jsonl found.")
        raw_path = existing[-1]
        click.echo(f"[1/6] Reusing existing fetch: {raw_path}")
        from openbro.training.data_sources import RawDoc

        docs = []
        with raw_path.open(encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                docs.append(
                    RawDoc(
                        source=d.get("source", ""),
                        id=d.get("id", ""),
                        title=d.get("title", ""),
                        body=d.get("body", ""),
                        url=d.get("url", ""),
                        tags=d.get("tags", []),
                        score=d.get("score", 0),
                    )
                )
        summary["fetch"] = {"count": len(docs), "raw_path": str(raw_path), "reused": True}

    # ─── Stage 2: Build dataset ─────────────────────────────────
    click.echo("[2/6] Building training.jsonl …")
    from openbro.training import dataset as _dataset

    dataset_path = queue_dir / "training.jsonl"
    ds_meta = _dataset.build(docs, dataset_path)
    summary["dataset"] = ds_meta
    click.echo(f"      {ds_meta['final_count']} examples (deduped from {ds_meta['pre_dedupe']})")

    # ─── Stage 3: Fine-tune ─────────────────────────────────────
    click.echo("[3/6] LoRA fine-tune (4-6 hours on RTX 3050 4 GB)…")
    from openbro.training import finetune as _finetune

    cfg = _finetune.FinetuneConfig(
        base_model=base_model,
        output_dir=str(runs_dir),
        dataset_path=str(dataset_path),
        epochs=1 if quick else 3,
    )
    summary["finetune"] = _finetune.run(cfg)

    # ─── Stage 4: Convert to GGUF ───────────────────────────────
    click.echo("[4/6] Merging LoRA + converting to GGUF (Q4_K_M)…")
    from openbro.training import to_gguf as _to_gguf

    gguf_path = runs_dir / DEFAULT_OUTPUT_NAME
    conv_cfg = _to_gguf.ConvertConfig(
        base_model=base_model,
        adapters_dir=str(runs_dir / "adapters"),
        merged_dir=str(runs_dir / "merged"),
        gguf_output=str(gguf_path),
    )
    summary["convert"] = _to_gguf.convert(conv_cfg)

    # ─── Stage 5: Validate ──────────────────────────────────────
    click.echo("[5/6] Smoke-testing the new GGUF…")
    from openbro.training import validate as _validate

    val = _validate.smoke_test(gguf_path)
    summary["validate"] = {"ok": val.ok, "failed": val.failed, "notes": val.notes}
    if not val.ok:
        click.echo(f"      FAILED: {val.failed}")
        click.echo("      Skipping install + publish to protect the live model.")
        (runs_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        raise click.ClickException("Validation failed.")
    click.echo("      OK")

    # Replace the live model.
    live_path = _to_gguf.install_into_models_dir(gguf_path, models_dir, DEFAULT_OUTPUT_NAME)
    summary["install"] = {"live_path": str(live_path)}
    click.echo(f"      Live model: {live_path}")

    # ─── Stage 6: Publish ───────────────────────────────────────
    if no_publish:
        click.echo("[6/6] --no-publish set; skipping git/HF push.")
    else:
        click.echo("[6/6] Pushing to GitHub + HuggingFace…")
        from openbro.training import publish as _publish

        pub_cfg = _publish.PublishConfig(
            gguf_path=str(live_path),
            run_id=run_id,
            repo_dir=str(root_p / "openbro-model"),
        )
        summary["publish"] = _publish.publish(pub_cfg)
        click.echo("      Done.")

    summary["finished"] = time.time()
    summary["total_seconds"] = summary["finished"] - summary["started"]
    (runs_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    click.echo(f"\n✓ Run {run_id} complete. Summary: {runs_dir / 'summary.json'}")
