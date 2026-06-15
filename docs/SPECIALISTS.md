# OpenBro Specialist Tree

> Tree-of-experts: one shared base model in VRAM + many small LoRA
> adapters, one per knowledge category. A tiny router maps each prompt
> to a category in microseconds; the adapter engine hot-swaps the
> matching LoRA onto the base. Everything runs offline.

## Why

A single 360M general model can't be good at *both* Python debugging
*and* mental-health support *and* cricket trivia. A 70 B model could,
but doesn't fit in 4 GB VRAM. The middle ground: keep the base small
and specialise per category with cheap LoRA adapters (~50 MB each).

```
                    ┌──────────────────────────┐
   user prompt ───→ │ Router (rules + embeds)  │ ←── categories.db
                    └──────────┬───────────────┘
                               ↓ slug
                    ┌──────────────────────────┐
                    │ AdapterEngine (one VRAM- │
                    │ resident base, LRU swap) │ ←── adapters/<slug>/
                    └──────────┬───────────────┘
                               ↓
                       generated response
```

## Files

- `openbro/specialists/db.py` — SQLite schema + seed of 70 categories
  (16 top-level + 54 sub).
- `openbro/specialists/router.py` — `Router.route(prompt)` →
  `RouteResult(slug, method, confidence, elapsed_ms, matched_keyword)`.
  Rule layer first (case-insensitive keyword match, longest hit wins);
  fall through to MiniLM-L6 cosine similarity if nothing matches.
- `openbro/specialists/adapter_engine.py` — `AdapterEngine.chat(prompt,
  slug)` loads the base model lazily, hot-swaps LoRA adapters with an
  LRU cache, and generates.
- `openbro/specialists/data.py` — per-category dataset builder. Pulls
  Dolly / Alpaca / SlimOrca / OpenHermes-2.5, keyword-filters into each
  leaf's JSONL.
- `openbro/specialists/train_all.py` — overnight loop: loads the base
  once, trains every `training_status='data_ready'` category in
  decreasing order of dataset size, saves each adapter under
  `adapters/<slug>/`.
- `openbro/specialists/verify.py` — probes each trained category with
  hand-written prompts, flags degenerate / empty / slow output.
- `openbro/llm/specialist_provider.py` — `LLMProvider` adapter so the
  rest of OpenBro can use the tree transparently.

## File layout on disk

```
D:/OpenBro-teting/specialists/
├── categories.db                  # SQLite source of truth
├── category_embeddings.npy        # cached MiniLM embeddings
├── datasets/
│   ├── coding-python.jsonl
│   ├── coding-javascript.jsonl
│   ├── …                          # one per leaf
├── adapters/
│   ├── coding-python/
│   │   └── adapter_model.safetensors
│   ├── …
├── training_runs/                 # per-slug Trainer outputs
├── train_log.json                 # append-only log
└── verify_report.json
```

## Routing model

**Tier 1 — rule match.** Every category row stores `keywords` (a
pipe-separated list). We scan the prompt for each keyword as a
case-insensitive substring. The longest keyword on the deepest tree
node wins. Cost: a few hundred string comparisons, <1 ms typical.

**Tier 2 — embedding fallback.** If no keyword fires, the router
encodes the prompt with `sentence-transformers/all-MiniLM-L6-v2`
(~22 MB), computes cosine similarity against the cached embeddings of
every category's `display_name + description`, picks the highest. The
embeddings are built lazily on first use and cached to disk.

**Last-resort fallback.** If even the embedder fails (e.g., the model
isn't installed), the router returns `general` with `method='fallback'`.

## Adapter hot-swap

Loading a 700 MB GGUF takes 30–90 s; switching LoRA adapters on an
already-loaded base takes ~100 ms. The engine keeps the base in VRAM
forever and cycles adapters in an LRU keyed by slug. Adapter cache size
defaults to 4 — bump if your category graph clusters into more than
that.

## Per-category training

Each leaf gets its own LoRA training run on the same base model:

```
SmolLM2-360M-Instruct  (in VRAM, frozen, NF4-quantised)
        + LoRA adapter (r=16, alpha=32, q/k/v/o projections)
        → 3.27 M trainable params per adapter
```

Training cap: `max_steps = min(400, n_examples // 8)` per category — a
hard ceiling so the overnight loop reaches every category even if one
has 25 K examples and another has 500.

The trainer uses `tokenizer.apply_chat_template` instead of hardcoding
Llama-3 chat tokens. This is the lesson from yesterday's degraded
v0.2/v0.3 runs: SmolLM2 expects ChatML, not Llama-3, and the wrong
template silently destroyed the fine-tune.

## Plug into OpenBro

Set the provider to `specialist` in `~/.openbro/config.yaml`:

```yaml
llm:
  provider: specialist
  fallback: google
providers:
  specialist:
    base_model: HuggingFaceTB/SmolLM2-360M-Instruct
    adapters_dir: D:/OpenBro-teting/specialists/adapters
```

Or via CLI:

```powershell
openbro config set llm.provider specialist
openbro --provider specialist
```

When `llm.fallback` is set, any local failure (degenerate output,
no specialist trained for the routed slug) bubbles up to the fallback
provider — same chain as the existing local/cloud pairing.

## What's deliberately *not* in scope

- **OpenAI-style function calling.** Local specialists call tools by
  emitting `<tool_call name="…">{…}</tool_call>` in plain text, parsed
  by the agent loop. The 360M model is too small to push schemas
  through a real function-calling API reliably; we stay on plain text
  and let the cloud fallback do anything fancier.
- **Cross-category reasoning.** A query like "Python script to
  calculate my BMI" routes to either `coding-python` or
  `health-nutrition` based on which keyword matched first — there's no
  ensemble. Add a `general` adapter or a cloud fallback for these.
- **Continual learning at runtime.** Re-running `train_all.py` retrains
  flagged categories; weights don't update mid-conversation.

## Scaling

The pipeline supports any number of categories without code changes —
add rows to `categories.db`, drop a JSONL into `datasets/<slug>.jsonl`,
re-run `train_all`. The bottleneck is GPU time:

  | Examples/cat | Steps capped at | RTX 3050 wall time |
  | -----------: | --------------: | -----------------: |
  |        500   |              60 |          ~5 min/cat |
  |      5,000   |             400 |         ~30 min/cat |
  |     25,000   |             400 |         ~30 min/cat |
  |    500,000   |           4,000 |         ~5 hr/cat   |

For larger per-category corpora (500 K +), a cloud A100 finishes a 70-
category sweep in under a day at ~₹1.5 K total — the local pipeline is
identical, just pointed at a bigger GPU.
