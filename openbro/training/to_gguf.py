"""Merge LoRA adapters into the base model and convert to GGUF.

After fine-tuning, the adapters live in `<run>/adapters/`. This stage
produces a single `openbro.gguf` file (~700 MB at Q4_K_M) that the
existing `LocalLLMProvider` can load directly via llama-cpp-python.

The conversion uses the `convert-hf-to-gguf.py` script that ships with
llama.cpp. The user must have llama.cpp built locally (or installed
via `pip install llama-cpp-python` plus pulling the conversion script
separately).

This module shells out to those external tools rather than re-
implementing them — they're the canonical source of truth for the
GGUF format and they update regularly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ConvertConfig:
    """Where the LoRA-merged model gets written and how it's quantised."""

    base_model: str
    adapters_dir: str
    merged_dir: str  # intermediate fp16 merge output
    gguf_output: str  # final openbro.gguf path
    quantization: str = "Q4_K_M"  # llama.cpp quantisation tier
    llama_cpp_dir: str = ""  # location of llama.cpp checkout (env override)


def merge_lora(config: ConvertConfig) -> None:
    """Load base + adapters via peft, merge weights, save as a
    standalone HuggingFace model under `merged_dir`. This is the
    intermediate step before GGUF conversion."""
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    Path(config.merged_dir).mkdir(parents=True, exist_ok=True)

    base = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        torch_dtype="auto",
        device_map="cpu",
    )
    merged = PeftModel.from_pretrained(base, config.adapters_dir)
    merged = merged.merge_and_unload()
    merged.save_pretrained(config.merged_dir, safe_serialization=True)
    AutoTokenizer.from_pretrained(config.adapters_dir).save_pretrained(config.merged_dir)


def convert_to_gguf(config: ConvertConfig) -> None:
    """Run llama.cpp's convert + quantize pipeline.

    Step 1: convert-hf-to-gguf.py  — fp16 GGUF (~2.5 GB)
    Step 2: llama-quantize          — Q4_K_M GGUF (~700 MB)

    `llama_cpp_dir` is the path to a llama.cpp checkout. Override via
    OPENBRO_LLAMA_CPP_DIR env var if it lives outside the default
    location.
    """
    import os as _os

    llama_dir = config.llama_cpp_dir or _os.environ.get("OPENBRO_LLAMA_CPP_DIR", "./llama.cpp")
    llama_dir_p = Path(llama_dir)
    if not llama_dir_p.exists():
        raise FileNotFoundError(
            f"llama.cpp checkout not found at {llama_dir}. Clone it: "
            "`git clone https://github.com/ggerganov/llama.cpp`. Then "
            "build via `make` (Linux/Mac) or `cmake --build .` "
            "(Windows). Or set OPENBRO_LLAMA_CPP_DIR to your existing "
            "checkout."
        )

    convert_py = llama_dir_p / "convert-hf-to-gguf.py"
    if not convert_py.exists():
        # Newer llama.cpp uses convert_hf_to_gguf.py (underscore).
        convert_py = llama_dir_p / "convert_hf_to_gguf.py"

    fp16_path = Path(config.gguf_output).with_suffix(".fp16.gguf")
    quant_bin = llama_dir_p / (
        "llama-quantize" if (llama_dir_p / "llama-quantize").exists() else "llama-quantize.exe"
    )
    if not quant_bin.exists():
        # Fall back to the legacy `quantize` name.
        quant_bin = llama_dir_p / (
            "quantize" if (llama_dir_p / "quantize").exists() else "quantize.exe"
        )

    # Step 1 — fp16 GGUF
    subprocess.run(
        [
            sys.executable,
            str(convert_py),
            config.merged_dir,
            "--outfile",
            str(fp16_path),
            "--outtype",
            "f16",
        ],
        check=True,
    )

    # Step 2 — quantise
    subprocess.run(
        [str(quant_bin), str(fp16_path), str(config.gguf_output), config.quantization],
        check=True,
    )

    # Cleanup intermediate fp16 file (saves ~2.5 GB disk).
    fp16_path.unlink(missing_ok=True)


def convert(config: ConvertConfig) -> dict[str, object]:
    """Run merge_lora + convert_to_gguf in sequence and return a
    summary dict for the publish step."""
    started = time.time()
    merge_lora(config)
    convert_to_gguf(config)
    size_bytes = Path(config.gguf_output).stat().st_size

    summary = {
        "elapsed_seconds": time.time() - started,
        "gguf_output": config.gguf_output,
        "quantization": config.quantization,
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 1),
    }
    out_dir = Path(config.gguf_output).parent
    (out_dir / "convert_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def install_into_models_dir(
    gguf_path: Path,
    target_models_dir: Path,
    target_name: str = "openbro.gguf",
) -> Path:
    """Move the freshly-built GGUF into the live models directory,
    backing up the previous copy. Returns the new live path."""
    target_models_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = target_models_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    live_path = target_models_dir / target_name
    if live_path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.move(
            str(live_path),
            str(backup_dir / f"{target_name}.{stamp}.bak"),
        )
    shutil.copy2(str(gguf_path), str(live_path))
    return live_path
