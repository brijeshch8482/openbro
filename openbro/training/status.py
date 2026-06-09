"""Read past training-run summaries for `openbro train --status`."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunInfo:
    """One past training run, reconstructed from its summary.json."""

    run_id: str
    started: float
    finished: float | None
    dataset_count: int
    train_loss: float | None
    gguf_size_mb: float | None
    pr_url: str | None
    hf_uploaded: bool


def _safe_get(d: dict, *keys: str, default=None):
    """Walk `keys` into nested dict; return default if any step missing."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def load_runs(root: Path) -> list[RunInfo]:
    """Find every training_runs/<id>/summary.json under `root` and
    parse into RunInfo. Returns oldest-first."""
    runs_dir = root / "training_runs"
    if not runs_dir.exists():
        return []
    out: list[RunInfo] = []
    for d in sorted(runs_dir.iterdir()):
        if not d.is_dir():
            continue
        sf = d / "summary.json"
        if not sf.exists():
            continue
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            continue
        pr = _safe_get(data, "publish", "pr", "url") or ""
        hf_uploaded = bool(_safe_get(data, "publish", "hf", "repo"))
        out.append(
            RunInfo(
                run_id=data.get("run_id", d.name),
                started=float(data.get("started", 0)),
                finished=(float(data["finished"]) if "finished" in data else None),
                dataset_count=int(_safe_get(data, "dataset", "final_count", default=0) or 0),
                train_loss=_safe_get(data, "finetune", "train_loss"),
                gguf_size_mb=_safe_get(data, "convert", "size_mb"),
                pr_url=pr,
                hf_uploaded=hf_uploaded,
            )
        )
    return out


def days_since_last(run: RunInfo) -> float:
    """How many days have passed since the latest run finished."""
    ref = run.finished or run.started
    return (time.time() - ref) / 86400.0
