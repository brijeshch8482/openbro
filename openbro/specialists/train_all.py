"""Overnight loop: train one LoRA adapter per data_ready category.

Order is by data-richness (most examples first) so the highest-impact
specialists ship even if we run out of GPU time. Every adapter lands
under D:/OpenBro-teting/specialists/adapters/<slug>/ and the row in
categories.db flips to training_status='trained' on success.

Resumable: rerunning skips slugs that are already 'trained' on disk.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import torch

# Same Windows-segfault dance: datasets first.
from datasets import load_dataset  # noqa: I001
from peft import (
    LoraConfig,
    PeftModel,
    get_peft_model,
    prepare_model_for_kbit_training,
)
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments,
)

from openbro.specialists.db import init_db

BASE_MODEL = "HuggingFaceTB/SmolLM2-360M-Instruct"
DATASETS_DIR = Path("D:/OpenBro-teting/specialists/datasets")
ADAPTERS_DIR = Path("D:/OpenBro-teting/specialists/adapters")
RUNS_DIR = Path("D:/OpenBro-teting/specialists/training_runs")
LOG_PATH = Path("D:/OpenBro-teting/specialists/train_log.json")


def _log(slug: str, payload: dict) -> None:
    if LOG_PATH.exists():
        log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    else:
        log = {"runs": []}
    log["runs"].append({"slug": slug, "ts": time.time(), **payload})
    LOG_PATH.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")


def _pending(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT slug, n_examples FROM categories "
        "WHERE training_status = 'data_ready' "
        "ORDER BY n_examples DESC"
    )
    return cur.fetchall()


def _build_dataset(tokenizer, dataset_path: Path, max_seq: int = 384):
    """Tokenise one slug's dataset using the tokenizer's chat
    template — same correctness fix that yesterday's bug forced."""
    ds = load_dataset("json", data_files=str(dataset_path), split="train")

    def _format(ex):
        text = tokenizer.apply_chat_template(
            [
                {"role": "user", "content": ex["input"]},
                {"role": "assistant", "content": ex["output"]},
            ],
            tokenize=False,
            add_generation_prompt=False,
        )
        return {"text": text}

    ds = ds.map(_format, remove_columns=ds.column_names)
    ds = ds.map(
        lambda b: tokenizer(b["text"], truncation=True, max_length=max_seq, padding=False),
        batched=True,
        remove_columns=["text"],
    )
    return ds


def train_one(
    slug: str,
    n_examples: int,
    base_model,
    tokenizer,
    epochs: int = 1,
    max_steps_cap: int = 400,
) -> dict:
    """Train one LoRA adapter for the given slug. Returns a summary
    dict that gets written to train_log.json + categories.db."""
    out_dir = RUNS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    adapter_dst = ADAPTERS_DIR / slug
    adapter_dst.mkdir(parents=True, exist_ok=True)
    dataset_path = DATASETS_DIR / f"{slug}.jsonl"

    print(f"\n=== {slug}: {n_examples} examples ===", flush=True)
    started = time.time()

    ds = _build_dataset(tokenizer, dataset_path)
    # Cap steps for big leaves so the overnight loop reaches every
    # category. 400 steps × batch=1 × accum=8 = ~3200 effective
    # examples per leaf.
    max_steps = min(max_steps_cap, max(50, len(ds) // 8))

    model = get_peft_model(
        base_model,
        LoraConfig(
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            bias="none",
            task_type="CAUSAL_LM",
        ),
    )
    model.print_trainable_parameters()

    args = TrainingArguments(
        output_dir=str(out_dir),
        max_steps=max_steps,
        num_train_epochs=epochs,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=2e-4,
        warmup_ratio=0.03,
        weight_decay=0.01,
        logging_steps=25,
        save_steps=max_steps,
        save_total_limit=1,
        fp16=True,
        seed=42,
        report_to="none",
    )
    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    trainer = Trainer(model=model, args=args, train_dataset=ds, data_collator=collator)
    train_result = trainer.train()
    model.save_pretrained(str(adapter_dst))

    elapsed = time.time() - started
    summary = {
        "elapsed_seconds": round(elapsed, 1),
        "train_loss": float(train_result.training_loss),
        "global_step": int(train_result.global_step),
        "n_examples_used": len(ds),
        "max_steps": max_steps,
        "adapter_dir": str(adapter_dst),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # Free LoRA-specific state and drop it so the next iteration gets
    # a fresh wrapping of the (still-loaded) base.
    if isinstance(model, PeftModel):
        del model
    import gc  # noqa: PLC0415

    gc.collect()
    torch.cuda.empty_cache()

    return summary


def main(
    max_categories: int | None = None,
    skip_done: bool = True,
) -> None:
    print(
        f"=== Specialist trainer starting at {time.strftime('%H:%M:%S')} ===",
        flush=True,
    )
    print(
        f"torch={torch.__version__} | cuda={torch.cuda.is_available()}",
        flush=True,
    )
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable — refusing to train on CPU.")

    conn = init_db()
    candidates = _pending(conn)
    if skip_done:
        candidates = [
            (slug, n)
            for slug, n in candidates
            if not (ADAPTERS_DIR / slug / "adapter_model.safetensors").exists()
        ]
    if max_categories:
        candidates = candidates[:max_categories]
    print(f"Categories to train: {len(candidates)}", flush=True)

    if not candidates:
        return

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, quantization_config=bnb, device_map="auto"
    )
    base_model = prepare_model_for_kbit_training(base_model)

    cur = conn.cursor()
    for slug, n in candidates:
        try:
            cur.execute(
                "UPDATE categories SET training_status = 'training' WHERE slug = ?",
                (slug,),
            )
            conn.commit()

            summary = train_one(slug, n, base_model, tokenizer)

            cur.execute(
                "UPDATE categories SET training_status='trained', "
                "adapter_path=?, last_trained=?, train_loss=? "
                "WHERE slug=?",
                (
                    str(ADAPTERS_DIR / slug),
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    summary["train_loss"],
                    slug,
                ),
            )
            conn.commit()
            _log(slug, {"ok": True, **summary})
            print(
                f"\n[{slug}] done in {summary['elapsed_seconds']}s "
                f"loss={summary['train_loss']:.3f}",
                flush=True,
            )
        except Exception as e:
            cur.execute(
                "UPDATE categories SET training_status='failed' WHERE slug=?",
                (slug,),
            )
            conn.commit()
            _log(slug, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            print(f"\n[{slug}] FAILED: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    import sys

    cap = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(max_categories=cap)
