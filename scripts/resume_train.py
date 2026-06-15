"""Resume the LoRA fine-tune from checkpoint-1500.

Skip the trainer's `resume_from_checkpoint` (silently crashes on
this stack), instead load the LoRA adapter weights from
checkpoint-1500's safetensors and train fresh. We lose ~500 steps
of optimizer momentum but keep the trained LoRA weights.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import torch  # noqa: E402

# IMPORTANT: import datasets BEFORE transformers — there's a C-extension
# DLL conflict on Windows with torch 2.6+cu124 if the order is reversed,
# causing a silent segfault during import.
from datasets import load_dataset  # noqa: I001
from peft import (  # noqa: E402
    PeftModel,
    prepare_model_for_kbit_training,
)
from transformers import (  # noqa: E402
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments,
)

RUN_ID = "20260609-141643"
ROOT = Path("D:/OpenBro-teting")
CHECKPOINT = ROOT / "training_runs" / RUN_ID / "checkpoint-1500"
OUTPUT_DIR = ROOT / "training_runs" / RUN_ID
DATASET = ROOT / "training_queue" / RUN_ID / "training.jsonl"
BASE_MODEL = "HuggingFaceTB/SmolLM2-360M-Instruct"


def main() -> None:
    started = time.time()
    print(
        f"=== RESUMING at {time.strftime('%H:%M:%S')} "
        f"| torch={torch.__version__} | cuda={torch.cuda.is_available()} ===",
        flush=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print("[1/5] Loading dataset…", flush=True)
    ds = load_dataset("json", data_files=str(DATASET), split="train")

    def _format(ex):
        return {
            "text": (
                "<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
                f"{ex['input']}<|eot_id|>"
                "<|start_header_id|>assistant<|end_header_id|>\n\n"
                f"{ex['output']}<|eot_id|>"
            )
        }

    ds = ds.map(_format, remove_columns=ds.column_names)
    ds = ds.map(
        lambda b: tokenizer(b["text"], truncation=True, max_length=512, padding=False),
        batched=True,
        remove_columns=["text"],
    )
    print(f"      {len(ds)} examples tokenised", flush=True)

    print("[2/5] Loading 4-bit base model…", flush=True)
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, quantization_config=bnb, device_map="auto"
    )
    base = prepare_model_for_kbit_training(base)

    print(f"[3/5] Loading LoRA adapter from {CHECKPOINT.name}…", flush=True)
    model = PeftModel.from_pretrained(base, str(CHECKPOINT), is_trainable=True)
    model.print_trainable_parameters()

    print("[4/5] Starting trainer (~2.5 hr expected for 1831 remaining steps)…", flush=True)
    args = TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=1,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=2e-4,
        warmup_ratio=0.03,
        weight_decay=0.01,
        logging_steps=25,
        save_steps=500,
        save_total_limit=2,
        fp16=True,
        seed=42,
        report_to="none",
    )
    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    trainer = Trainer(model=model, args=args, train_dataset=ds, data_collator=collator)
    result = trainer.train()

    print("[5/5] Saving adapters…", flush=True)
    model.save_pretrained(str(OUTPUT_DIR / "adapters"))
    tokenizer.save_pretrained(str(OUTPUT_DIR / "adapters"))

    elapsed = time.time() - started
    summary = {
        "elapsed_seconds": elapsed,
        "elapsed_hours": round(elapsed / 3600, 2),
        "train_loss": float(result.training_loss),
        "global_step": int(result.global_step),
        "output_dir": str(OUTPUT_DIR),
        "adapters_dir": str(OUTPUT_DIR / "adapters"),
    }
    print(flush=True)
    print("=== RESUME COMPLETE ===", flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
