"""Custom model training pipeline for openbro.gguf.

See docs/TRAINING.md for the full architecture. The high-level flow:

  openbro train
      → data_sources.fetch_all()    # public APIs
      → dataset.build()              # JSONL training set
      → finetune.run()               # LoRA on Llama-3.2-1B
      → to_gguf.convert()            # quantize + GGUF
      → validate.smoke_test()        # sanity checks
      → publish.push()               # auto-PR + HuggingFace

Manually triggered by the maintainer. Public data sources only. Local
GPU (RTX 3050 4 GB VRAM verified).
"""

from __future__ import annotations

# Module version — used in training-run metadata and HuggingFace
# release notes. Bump when the pipeline contract changes.
PIPELINE_VERSION = "0.1.0"

# Default base model. Apache 2.0 license, ~700 MB after Q4 quantization,
# fits comfortably in 4 GB VRAM for LoRA fine-tuning.
DEFAULT_BASE_MODEL = "meta-llama/Llama-3.2-1B-Instruct"

# Where the produced openbro.gguf lives (used both by the training
# pipeline and by the LLM provider at runtime).
DEFAULT_OUTPUT_NAME = "openbro.gguf"
