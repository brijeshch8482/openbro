"""LoRA fine-tuning script for openbro.gguf.

Targets a 4 GB VRAM GPU (RTX 3050 verified). The hyper-parameters
below are chosen so that:

  base model (Llama-3.2-1B)            ≈ 2.5 GB in fp16
  4-bit NF4 quantization               drops to ~700 MB
  LoRA adapters (r=16)                 + ~16 MB
  optimiser state + gradients (8-bit)  + ~150 MB
  batch_size=2 × seq_len=1024          + ~600 MB
  ─────────────────────────────────────────────────
  Peak VRAM                            ~ 3.6 GB → fits in 4 GB

Runtime on RTX 3050 with ~20k training examples and 3 epochs is
roughly 4–6 hours. Use `--quick` (1 epoch) for debug iterations.

The actual heavy lifting is done by the `transformers` + `peft` +
`bitsandbytes` stack. This file's job is just to wire them together
deterministically — same dataset + same config = same output adapters.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class FinetuneConfig:
    """Hyper-parameters for one fine-tuning run. Persisted alongside
    the adapters so the run is reproducible."""

    base_model: str = "meta-llama/Llama-3.2-1B-Instruct"
    output_dir: str = "./training_runs/current"
    dataset_path: str = "./training_queue/current/training.jsonl"

    # LoRA settings — r=16 is the sweet spot for 1B models.
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: tuple[str, ...] = (
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
    )

    # Training hyper-params.
    epochs: int = 3
    learning_rate: float = 2e-4
    batch_size: int = 2
    gradient_accumulation: int = 8
    max_seq_length: int = 1024
    warmup_ratio: float = 0.03
    weight_decay: float = 0.01

    # Quantization (bitsandbytes).
    load_in_4bit: bool = True
    bnb_4bit_quant_type: str = "nf4"
    bnb_4bit_compute_dtype: str = "bfloat16"

    # Misc.
    seed: int = 42
    logging_steps: int = 25
    save_steps: int = 500
    fp16: bool = True


def run(config: FinetuneConfig) -> dict[str, object]:
    """Run a LoRA fine-tune end-to-end. Returns a result dict the
    publish step uses for the release notes.

    Imports the heavy ML stack lazily — this module should be safe to
    import even on a machine without CUDA / PyTorch installed.
    """
    started = time.time()

    # Lazy imports so the rest of OpenBro doesn't pay the cost.
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
    )

    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA is not available. LoRA fine-tuning on CPU is not "
            "practical at this model size. Install PyTorch with CUDA "
            'support and verify with `python -c "import torch; '
            'print(torch.cuda.is_available())"`.'
        )

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")

    # ─── 1. Tokenizer ────────────────────────────────────────────
    tokenizer = AutoTokenizer.from_pretrained(config.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # ─── 2. Dataset ──────────────────────────────────────────────
    ds = load_dataset("json", data_files=config.dataset_path, split="train")

    # Use the tokenizer's own chat_template — each base model ships
    # the right template (Llama-3 ones use <|start_header_id|>...,
    # SmolLM2 uses ChatML <|im_start|>..., Qwen and Mistral have
    # their own). The previous hardcoded Llama-3 format silently
    # corrupted training on SmolLM2 (captured 2026-06-10: model
    # degenerated to repeating the user's cwd name).
    if tokenizer.chat_template is None:
        raise RuntimeError(
            f"Base model {config.base_model} has no chat_template — "
            "set tokenizer.chat_template explicitly or pick a base "
            "model whose tokenizer ships one."
        )

    def _format(example):
        prompt = example["input"]
        response = example["output"]
        text = tokenizer.apply_chat_template(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response},
            ],
            tokenize=False,
            add_generation_prompt=False,
        )
        return {"text": text}

    ds = ds.map(_format, remove_columns=ds.column_names)

    def _tokenize(batch):
        return tokenizer(
            batch["text"],
            truncation=True,
            max_length=config.max_seq_length,
            padding=False,
        )

    ds = ds.map(_tokenize, batched=True, remove_columns=["text"])

    # ─── 3. Quantised base model ─────────────────────────────────
    bnb = BitsAndBytesConfig(
        load_in_4bit=config.load_in_4bit,
        bnb_4bit_quant_type=config.bnb_4bit_quant_type,
        bnb_4bit_compute_dtype=getattr(torch, config.bnb_4bit_compute_dtype),
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        quantization_config=bnb,
        device_map="auto",
    )
    model = prepare_model_for_kbit_training(model)

    # ─── 4. LoRA wrapper ─────────────────────────────────────────
    lora = LoraConfig(
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=list(config.target_modules),
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    # ─── 5. Trainer ──────────────────────────────────────────────
    args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=config.epochs,
        per_device_train_batch_size=config.batch_size,
        gradient_accumulation_steps=config.gradient_accumulation,
        learning_rate=config.learning_rate,
        warmup_ratio=config.warmup_ratio,
        weight_decay=config.weight_decay,
        logging_steps=config.logging_steps,
        save_steps=config.save_steps,
        save_total_limit=2,
        fp16=config.fp16,
        seed=config.seed,
        report_to="none",
    )

    # Right-pad collator — peft handles attention masks correctly.
    from transformers import DataCollatorForLanguageModeling

    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    trainer = Trainer(model=model, args=args, train_dataset=ds, data_collator=collator)
    train_result = trainer.train()

    # ─── 6. Persist adapters + tokenizer ─────────────────────────
    model.save_pretrained(str(output_dir / "adapters"))
    tokenizer.save_pretrained(str(output_dir / "adapters"))

    elapsed = time.time() - started
    summary = {
        "elapsed_seconds": elapsed,
        "elapsed_hours": round(elapsed / 3600, 2),
        "train_loss": float(train_result.training_loss),
        "global_step": int(train_result.global_step),
        "output_dir": str(output_dir),
        "adapters_dir": str(output_dir / "adapters"),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
