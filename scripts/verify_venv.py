"""Sanity-check the D-drive venv: every ML package imports + CUDA is
visible. Run via train.bat / train.ps1 so the env vars are right."""

from __future__ import annotations

import os
import sys


def main() -> int:
    print(f"Python:     {sys.executable}")
    print(f"Version:    {sys.version.split()[0]}")
    print()

    # Confirm caches point at D.
    cache_vars = (
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "HF_DATASETS_CACHE",
        "TORCH_HOME",
        "PIP_CACHE_DIR",
        "TMP",
        "TEMP",
        "OPENBRO_LLAMA_CPP_DIR",
    )
    print("Environment:")
    for v in cache_vars:
        val = os.environ.get(v, "<unset>")
        ok = val.startswith("D:") if val != "<unset>" else False
        print(f"  {'OK' if ok else '!!'}  {v:<25} {val}")
    print()

    # Import the heavy stack in the order that worked overnight
    # (datasets before transformers).
    targets = [
        "datasets",
        "torch",
        "peft",
        "transformers",
        "bitsandbytes",
        "accelerate",
        "huggingface_hub",
        "llama_cpp",
        "sentencepiece",
        "openbro.training.finetune",
    ]
    print("Imports:")
    ok = 0
    for t in targets:
        try:
            __import__(t)
            print(f"  OK  {t}")
            ok += 1
        except Exception as e:
            print(f"  !!  {t}  ({type(e).__name__}: {e})")
    print(f"\n  {ok}/{len(targets)} imports OK")

    # CUDA check.
    try:
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1)
            print(f"\nCUDA: OK — {name} ({vram} GB VRAM)")
        else:
            print("\nCUDA: NOT available (torch sees CPU only)")
            return 1
    except Exception as e:
        print(f"\nCUDA: FAILED — {e}")
        return 1

    print("\n✓ Venv ready for training.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
