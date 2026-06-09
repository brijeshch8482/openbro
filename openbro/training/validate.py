"""Smoke-test a freshly built openbro.gguf.

Quick checks that don't require the full agent stack:

- File loads via llama-cpp-python without exceptions.
- Model responds to a basic prompt in under N seconds.
- Output isn't degenerate (all whitespace, single repeated char).

If any check fails, the pipeline pauses before installing the new
model so the previous working copy stays live.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ValidationResult:
    ok: bool
    failed: list[str]
    notes: dict[str, object]


_QUICK_PROMPTS = [
    "Say OK in one word.",
    "What is 2+2? Answer with just the digit.",
    "List two file operations.",
]


def smoke_test(gguf_path: Path, timeout_seconds: float = 30.0) -> ValidationResult:
    """Run a tiny battery of prompts and confirm the model produces
    plausible output. Returns a structured result the pipeline reads."""
    failed: list[str] = []
    notes: dict[str, object] = {"gguf_path": str(gguf_path)}

    if not Path(gguf_path).exists():
        return ValidationResult(False, ["missing-file"], notes)

    try:
        from llama_cpp import Llama
    except ImportError:
        return ValidationResult(False, ["llama-cpp-python not installed"], notes)

    started = time.time()
    try:
        llm = Llama(model_path=str(gguf_path), n_ctx=2048, verbose=False)
    except Exception as e:
        return ValidationResult(False, [f"load-failed: {e}"], notes)
    notes["load_seconds"] = round(time.time() - started, 2)

    for prompt in _QUICK_PROMPTS:
        t0 = time.time()
        try:
            out = llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=64,
                temperature=0.1,
            )
        except Exception as e:
            failed.append(f"chat-failed[{prompt!r}]: {e}")
            continue
        elapsed = time.time() - t0
        if elapsed > timeout_seconds:
            failed.append(f"too-slow[{prompt!r}]: {elapsed:.1f}s")
            continue
        text = out.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            failed.append(f"empty-output[{prompt!r}]")
            continue
        if len(set(text.replace(" ", ""))) < 2:
            failed.append(f"degenerate-output[{prompt!r}]: {text[:80]}")
            continue

    notes["prompts_tested"] = len(_QUICK_PROMPTS)
    notes["prompts_failed"] = len(failed)
    return ValidationResult(ok=(not failed), failed=failed, notes=notes)
