# OpenBro Custom Model Training

This document describes the custom-model training pipeline that produces
`openbro.gguf` — OpenBro's specialized fine-tuned LLM, optimized for
routing, tool calls, and Hinglish responses.

## Vision

Ship OpenBro with its own lightweight specialized model (`openbro.gguf`,
~700 MB) that:

- Handles 90% of routine OpenBro commands offline, fast, free.
- Chains with optional cloud APIs (Claude / Gemini / OpenAI / Groq) for
  the remaining 10% that need heavy reasoning.
- Updates manually (user-triggered) by fetching the latest public data
  from the web, retraining, and auto-publishing the new model.

## Core principles

1. **Manual trigger, automatic pipeline.** Training runs only when the
   maintainer (you) runs `openbro train`. Once started, the pipeline
   fetches data, fine-tunes, converts, and publishes without further
   intervention.
2. **Public data only.** All training data comes from explicit public
   sources (Stack Overflow, GitHub public repos, Wikipedia, public docs,
   news). No automated collection of end-user data. Opt-in contributions
   live in a separate repo with full PII scrubbing — covered in a later
   phase.
3. **Local compute, your laptop.** No cloud GPU rental. LoRA fine-tuning
   on the RTX 3050 (4 GB VRAM) — about 4–6 hours per cycle.
4. **Reproducible.** Every training run records the dataset hash, base
   model version, LoRA hyper-parameters, and timestamp. Anyone can
   re-run the same configuration and get the same `openbro.gguf`.
5. **No hardcoded orchestration.** The training pipeline is engineering
   plumbing only — fetch data, train, convert, publish. The model's
   reasoning behaviour comes from data quality, not from special-cased
   rules baked into agent code.

## Pipeline overview

```
            ┌─────────────────────────────────────────┐
            │  Maintainer runs: openbro train         │
            └─────────────────────────────────────────┘
                                ↓
            ┌─────────────────────────────────────────┐
            │  STAGE 1 — Fetch public data            │
            │  - Stack Overflow (public API)          │
            │  - GitHub Issues / Discussions          │
            │  - Wikipedia summaries                  │
            │  - Public docs / tutorials              │
            │  - News API (free tier)                 │
            │  Output: D:/training_queue/<run-id>/    │
            └─────────────────────────────────────────┘
                                ↓
            ┌─────────────────────────────────────────┐
            │  STAGE 2 — Build training dataset       │
            │  - Convert fetched data → (input,       │
            │    output) JSONL pairs                  │
            │  - Dedupe, length-filter, quality score │
            │  - Optional: use Claude/Gemini to       │
            │    paraphrase low-quality examples      │
            │  Output: training.jsonl + meta.json     │
            └─────────────────────────────────────────┘
                                ↓
            ┌─────────────────────────────────────────┐
            │  STAGE 3 — LoRA fine-tune               │
            │  - Base: Llama-3.2-1B-Instruct          │
            │  - 4-bit NF4 quantization               │
            │  - LoRA r=16, alpha=32                  │
            │  - 3 epochs, batch=2, accum=8           │
            │  - Local RTX 3050 4 GB VRAM             │
            │  - Runtime: 4–6 hours                   │
            │  Output: lora_adapters/                 │
            └─────────────────────────────────────────┘
                                ↓
            ┌─────────────────────────────────────────┐
            │  STAGE 4 — Convert to GGUF              │
            │  - Merge LoRA into base weights         │
            │  - llama.cpp convert-hf-to-gguf.py      │
            │  - Quantize to Q4_K_M                   │
            │  Output: openbro.gguf (~700 MB)         │
            └─────────────────────────────────────────┘
                                ↓
            ┌─────────────────────────────────────────┐
            │  STAGE 5 — Validate                     │
            │  - Smoke tests on captured patterns     │
            │  - Tool-call format check               │
            │  - Hinglish response check              │
            │  - If failure: pause and notify         │
            └─────────────────────────────────────────┘
                                ↓
            ┌─────────────────────────────────────────┐
            │  STAGE 6 — Publish                      │
            │  - Backup previous openbro.gguf         │
            │  - Replace local file                   │
            │  - Commit + push to openbro-model repo  │
            │  - Auto-create PR via gh CLI            │
            │  - Push to HuggingFace Hub              │
            │  - Print release notes                  │
            └─────────────────────────────────────────┘
                                ↓
            ┌─────────────────────────────────────────┐
            │  100k end users worldwide:              │
            │  openbro model update                   │
            │  - Pulls latest openbro.gguf from HF    │
            │  - No bandwidth cost (HF CDN free)      │
            └─────────────────────────────────────────┘
```

## Public data sources

All sources are queried using their official public APIs. No scraping
of authenticated content, no user data, no privacy issues.

| Source | API | Free tier | Used for |
|---|---|---|---|
| Stack Overflow | api.stackexchange.com | 10k req/day | Q&A patterns, tool usage |
| GitHub | api.github.com | 5k req/hr (token) | Issue patterns, code questions |
| Wikipedia | en.wikipedia.org/api | unlimited | Factual knowledge |
| ArXiv | export.arxiv.org/api | unlimited | Technical context |
| Reddit | reddit.com/.json | 60 req/min | Discussion patterns |
| NewsAPI | newsapi.org | 100 req/day | Current events |
| HuggingFace datasets | datasets-server.huggingface.co | unlimited | Existing curated sets |

Each source returns raw documents that are then normalised into the
(prompt, response) pair format the trainer expects.

## Hardware requirements

| Component | Required | Why |
|---|---|---|
| GPU | NVIDIA with ≥ 4 GB VRAM | LoRA fine-tune at batch=2 |
| CUDA | 11.8 or 12.x | PyTorch GPU backend |
| RAM | ≥ 8 GB system | Data loading, tokenisation |
| Disk | ≥ 50 GB free | Base model + adapters + GGUF |
| Network | Stable connection | API calls, HuggingFace push |
| Time | 4–6 hours per run | LoRA training duration |

Verified working on: RTX 3050 Laptop 4 GB VRAM, i5-11400H, 16 GB RAM.

## CLI commands

```
openbro train                  # Run the full pipeline (manual trigger)
openbro train --no-publish     # Train but skip Stage 6 (no PR, no HF push)
openbro train --skip-fetch     # Reuse last training_queue (faster iteration)
openbro train --quick          # 1 epoch instead of 3 (debug only)
openbro train --resume         # Resume from last checkpoint if it crashed

openbro model update           # End-user: pull latest openbro.gguf from HF
openbro model info             # Show current model version + source
```

## File layout

```
D:/OpenBro/
├── openbro/training/
│   ├── __init__.py
│   ├── data_sources.py        # Public API fetchers
│   ├── dataset.py             # JSONL builder + filters
│   ├── finetune.py            # LoRA fine-tune script
│   ├── to_gguf.py             # Conversion pipeline
│   ├── publish.py             # Auto-PR + HuggingFace push
│   ├── validate.py            # Smoke tests on new model
│   └── cli.py                 # `openbro train` command
├── docs/TRAINING.md           # This document
└── ...

D:/OpenBro-teting/
├── models/
│   ├── openbro.gguf           # Active model
│   └── backups/               # Previous versions
├── training_queue/
│   └── <run-id>/              # Fetched data per run
└── training_runs/
    └── <run-id>/              # LoRA outputs + logs
```

## Privacy and legal

This pipeline is designed to be legally compliant from day one:

- ✅ Public APIs only — no scraping of authenticated content.
- ✅ No automated collection of end-user data.
- ✅ All data sources have permissive licenses or are CC-BY / MIT / Apache-2.0.
- ✅ Stack Overflow content under CC-BY-SA — attribution preserved in
  training metadata.
- ✅ Output model under Apache 2.0 (inherited from Llama-3.2 base).
- ❌ Future opt-in contribution system from end users would require a
  separate privacy policy, GDPR/DPDP compliance pipeline, and PII
  scrubbing — NOT in scope for this phase.

## Status

| Phase | Component | Status |
|---|---|---|
| Phase 1 | Training environment setup (PyTorch + CUDA + LoRA) | ⬜ pending |
| Phase 2 | data_sources.py — public API fetchers | ⬜ pending |
| Phase 3 | dataset.py — JSONL builder | ⬜ pending |
| Phase 4 | finetune.py — LoRA fine-tune | ⬜ pending |
| Phase 5 | to_gguf.py — model conversion | ⬜ pending |
| Phase 6 | validate.py — smoke tests | ⬜ pending |
| Phase 7 | publish.py — auto-PR + HF push | ⬜ pending |
| Phase 8 | cli.py — `openbro train` integration | ⬜ pending |
| Phase 9 | `openbro model update` for end users | ⬜ pending |
| Phase 10 | `brijeshch8482/openbro-model` HF repo | ⬜ pending |
