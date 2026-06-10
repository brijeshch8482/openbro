"""Overnight orchestrator — waits for the in-flight LoRA training to
finish, then auto-runs the next stages without supervision:

  1. Wait for current run (20260609-141643) to write summary.json.
  2. Merge LoRA + convert to GGUF → openbro.gguf v0.2.
  3. Back up v0.2 to models/backups/.
  4. Install v0.2 as live.
  5. Fetch tool-calling dataset (xLAM 60K + synthesised OpenBro tools).
  6. Continue training from v0.2 adapters on tools dataset (1 epoch).
  7. Merge + convert → openbro.gguf v0.3.
  8. Back up v0.3, install v0.3 as live.
  9. Write final_report.json.

Designed to run unattended overnight. Every stage logs to
overnight_report.json so the morning user can inspect what happened.
"""

from __future__ import annotations

# CRITICAL: datasets BEFORE transformers (silent segfault otherwise).
from datasets import load_dataset  # noqa: I001

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("D:/OpenBro-teting")
RUN_ID_V2 = "20260609-141643"  # the in-flight run
RUN_DIR_V2 = ROOT / "training_runs" / RUN_ID_V2
MODELS_DIR = ROOT / "models"
BACKUPS_DIR = MODELS_DIR / "backups"
LIVE_PATH = MODELS_DIR / "openbro.gguf"

REPORT = ROOT / "overnight_report.json"
LOG_PATH = ROOT / "overnight.log"


def log(msg: str) -> None:
    """Append to the overnight log + print for visibility."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def update_report(stage: str, data: dict) -> None:
    """Merge `data` into a per-stage section of the overnight report."""
    if REPORT.exists():
        report = json.loads(REPORT.read_text(encoding="utf-8"))
    else:
        report = {"started": time.time(), "stages": {}}
    report["stages"][stage] = data
    report["last_update"] = time.time()
    REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


# ─── Stage 1 — Wait for v0.2 training to finish ──────────────────────


def wait_for_v2() -> dict:
    """Block until the in-flight training run writes its summary.json.
    Returns the summary dict."""
    log(f"Stage 1: waiting for {RUN_DIR_V2}/summary.json")
    sf = RUN_DIR_V2 / "summary.json"
    waited = 0
    while not sf.exists():
        time.sleep(60)
        waited += 60
        if waited % 600 == 0:
            log(f"  …still waiting ({waited // 60} min so far)")
    summary = json.loads(sf.read_text(encoding="utf-8"))
    log(f"  v0.2 training complete — loss={summary.get('train_loss')}")
    update_report("v2_train", summary)
    return summary


# ─── Stage 2/4 — Convert + install ──────────────────────────────────


def convert_to_gguf(run_dir: Path, label: str) -> Path:
    """Merge LoRA into base, convert to GGUF Q4_K_M, return the path
    of the produced openbro.gguf."""
    log(f"Stage convert ({label}): merging LoRA + converting")
    os.environ.setdefault("OPENBRO_LLAMA_CPP_DIR", "D:/llama.cpp")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from openbro.training import to_gguf  # imported here so dataset comes first

    cfg = to_gguf.ConvertConfig(
        base_model="HuggingFaceTB/SmolLM2-360M-Instruct",
        adapters_dir=str(run_dir / "adapters"),
        merged_dir=str(run_dir / "merged"),
        gguf_output=str(run_dir / "openbro.gguf"),
        quantization="Q4_K_M",
    )
    to_gguf.merge_lora(cfg)
    to_gguf.convert_to_gguf(cfg)
    gguf = run_dir / "openbro.gguf"
    size_mb = round(gguf.stat().st_size / (1024 * 1024), 1)
    log(f"  GGUF built: {gguf} ({size_mb} MB)")
    update_report(
        f"{label}_convert",
        {"gguf_path": str(gguf), "size_mb": size_mb},
    )
    return gguf


def install_live(gguf: Path, label: str) -> Path:
    """Back up previous live model, copy this one in as openbro.gguf."""
    log(f"Stage install ({label}): backing up + replacing live model")
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    if LIVE_PATH.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bak = BACKUPS_DIR / f"openbro.gguf.{stamp}.{label}.bak"
        shutil.move(str(LIVE_PATH), str(bak))
        log(f"  previous live → {bak.name}")
    shutil.copy2(str(gguf), str(LIVE_PATH))
    # Also keep a labelled copy alongside backups so morning user can
    # roll back without hunting through timestamps.
    shutil.copy2(str(gguf), str(BACKUPS_DIR / f"openbro.gguf.{label}"))
    log(f"  live model: {LIVE_PATH}")
    update_report(f"{label}_install", {"live_path": str(LIVE_PATH)})
    return LIVE_PATH


# ─── Stage 5 — Build tools dataset ──────────────────────────────────


# Hand-written OpenBro-specific tool patterns. Generic xLAM examples
# don't know our tool names; these teach the model to call ours.
_OPENBRO_TOOL_EXAMPLES = [
    {
        "input": "mere C drive me kitna space hai?",
        "output": (
            "Mai system_health tool chalata.\n"
            '<tool_call name="system_health">{"drive": "C"}</tool_call>'
        ),
    },
    {
        "input": "kitne PDF files Desktop par hain?",
        "output": (
            "Mai file_search tool se Desktop par PDFs dhundhta.\n"
            '<tool_call name="file_search">'
            '{"path": "~/Desktop", "pattern": "*.pdf"}</tool_call>'
        ),
    },
    {
        "input": "chrome chal raha kya?",
        "output": (
            "Mai process_check se dekhta.\n"
            '<tool_call name="process_check">{"name": "chrome"}</tool_call>'
        ),
    },
    {
        "input": "spotify kholo",
        "output": (
            '<tool_call name="open_app">{"name": "spotify"}</tool_call>'
        ),
    },
    {
        "input": "browser band kar do",
        "output": (
            '<tool_call name="close_app">{"name": "chrome"}</tool_call>'
        ),
    },
    {
        "input": "openbro kya hai?",
        "output": (
            "OpenBro tera personal AI agent hai bhai — pure offline kaam karta, "
            "Hinglish me baat karta, tera laptop control kar sakta. JARVIS jaisa."
        ),
    },
    {
        "input": "kon ho tum?",
        "output": (
            "OpenBro hu bhai, tera open-source personal AI agent. Tera laptop "
            "control kar sakta, files dhundh sakta, apps khol sakta, code likh sakta."
        ),
    },
    {
        "input": "kya tum web search kar sakte ho?",
        "output": (
            "Haan bhai, web_search tool hai. Bata kya search karna.\n"
            '<tool_call name="web_search">{"query": "<your query>"}</tool_call>'
        ),
    },
    {
        "input": "battery kitni hai?",
        "output": (
            '<tool_call name="battery_status">{}</tool_call>'
        ),
    },
    {
        "input": "screenshot lo",
        "output": (
            '<tool_call name="screenshot">{"path": "~/Desktop/screenshot.png"}</tool_call>'
        ),
    },
    {
        "input": "Excel file kholo",
        "output": (
            'Kaunsi file? Path bata, mai open_app se launch kar dunga.\n'
            '<tool_call name="open_app">{"name": "excel"}</tool_call>'
        ),
    },
    {
        "input": "kal ka meeting schedule check karo",
        "output": (
            '<tool_call name="calendar_check">{"date": "tomorrow"}</tool_call>'
        ),
    },
    {
        "input": "email check karo",
        "output": (
            '<tool_call name="email_check">{"unread_only": true}</tool_call>'
        ),
    },
    {
        "input": "Word me ek letter likho",
        "output": (
            "Kis topic pe? Mai content tayyar kar ke Word me save kar dunga.\n"
            '<tool_call name="create_document">'
            '{"app": "word", "topic": "<your topic>"}</tool_call>'
        ),
    },
    {
        "input": "system me kya issues hain?",
        "output": (
            '<tool_call name="system_health">{}</tool_call>'
        ),
    },
]


def build_tools_dataset() -> Path:
    """Pull xLAM function-calling + add OpenBro-specific examples,
    save as JSONL. Returns the dataset path."""
    log("Stage 5: building tools dataset")
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "training_queue" / f"{run_id}-tools"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "training.jsonl"

    n_xlam = 0
    n_openbro = 0

    try:
        ds = load_dataset(
            "Salesforce/xlam-function-calling-60k",
            split="train[:15000]",
        )
        log(f"  xLAM: {len(ds)} rows pulled")
    except Exception as e:
        log(f"  xLAM fetch failed: {e}")
        ds = []

    with out_path.open("w", encoding="utf-8") as f:
        for row in ds:
            # xLAM rows have 'query' (user prompt) and 'answers' (JSON
            # list of tool calls) — convert to the same input/output
            # shape our tokeniser expects.
            q = row.get("query", "").strip()
            ans = row.get("answers", "").strip()
            if not q or not ans:
                continue
            ex = {
                "input": q,
                "output": f"<tool_call>{ans}</tool_call>",
                "_source": "xlam",
            }
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
            n_xlam += 1
        # OpenBro-specific examples — repeat them 30x so the small
        # model sees enough signal alongside the 15K xLAM mass.
        for example in _OPENBRO_TOOL_EXAMPLES * 30:
            ex = {**example, "_source": "openbro"}
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
            n_openbro += 1

    log(f"  wrote {n_xlam + n_openbro} examples → {out_path}")
    update_report(
        "tools_dataset",
        {"path": str(out_path), "xlam": n_xlam, "openbro": n_openbro},
    )
    return out_path


# ─── Stage 6 — Continue training on tools ────────────────────────────


def train_tools(starting_adapters_dir: Path, dataset_path: Path) -> Path:
    """Load v0.2 adapters as starting weights and fine-tune for one
    epoch on the tools dataset. Returns the new run directory."""
    log("Stage 6: training on tools dataset")
    import torch  # noqa: PLC0415
    from peft import (  # noqa: PLC0415
        LoraConfig,
        PeftModel,
        prepare_model_for_kbit_training,
    )
    from transformers import (  # noqa: PLC0415
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
    )

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-tools"
    run_dir = ROOT / "training_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    base = "HuggingFaceTB/SmolLM2-360M-Instruct"
    tokenizer = AutoTokenizer.from_pretrained(base)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    ds = load_dataset("json", data_files=str(dataset_path), split="train")

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
        lambda b: tokenizer(
            b["text"], truncation=True, max_length=512, padding=False
        ),
        batched=True,
        remove_columns=["text"],
    )

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    base_m = AutoModelForCausalLM.from_pretrained(
        base, quantization_config=bnb, device_map="auto"
    )
    base_m = prepare_model_for_kbit_training(base_m)
    model = PeftModel.from_pretrained(
        base_m, str(starting_adapters_dir), is_trainable=True
    )
    model.print_trainable_parameters()

    args = TrainingArguments(
        output_dir=str(run_dir),
        num_train_epochs=1,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=1e-4,  # lower than fresh — continuing existing weights
        warmup_ratio=0.03,
        weight_decay=0.01,
        logging_steps=50,
        save_steps=1000,
        save_total_limit=2,
        fp16=True,
        seed=42,
        report_to="none",
    )
    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    trainer = Trainer(
        model=model, args=args, train_dataset=ds, data_collator=collator
    )
    started = time.time()
    result = trainer.train()
    elapsed = time.time() - started

    model.save_pretrained(str(run_dir / "adapters"))
    tokenizer.save_pretrained(str(run_dir / "adapters"))

    summary = {
        "elapsed_seconds": elapsed,
        "elapsed_hours": round(elapsed / 3600, 2),
        "train_loss": float(result.training_loss),
        "global_step": int(result.global_step),
        "output_dir": str(run_dir),
        "adapters_dir": str(run_dir / "adapters"),
        "dataset_path": str(dataset_path),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    log(f"  tools training complete in {summary['elapsed_hours']} hr — loss={summary['train_loss']:.3f}")
    update_report("tools_train", summary)
    return run_dir


# ─── Stage 9 — Final report ──────────────────────────────────────────


def write_final_report() -> None:
    """Snapshot of every model on disk + final report timestamp."""
    log("Stage 9: writing final report")
    final = {}
    final["models_on_disk"] = sorted(p.name for p in MODELS_DIR.glob("*.gguf"))
    final["backups"] = sorted(p.name for p in BACKUPS_DIR.glob("*"))
    final["live_size_mb"] = (
        round(LIVE_PATH.stat().st_size / (1024 * 1024), 1)
        if LIVE_PATH.exists()
        else None
    )
    final["finished_at"] = time.time()
    update_report("final", final)
    log("Overnight orchestrator finished.")


# ─── Main ────────────────────────────────────────────────────────────


def main() -> None:
    log("=== Overnight orchestrator started ===")
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Stage 1: wait for v0.2 to finish.
        wait_for_v2()

        # Stage 2: convert v0.2 to GGUF.
        v2_gguf = convert_to_gguf(RUN_DIR_V2, "v0.2")

        # Stage 3-4: install v0.2 live.
        install_live(v2_gguf, "v0.2")

        # Stage 5: tools dataset.
        tools_dataset = build_tools_dataset()

        # Stage 6: continue training on tools.
        tools_run_dir = train_tools(
            RUN_DIR_V2 / "adapters",
            tools_dataset,
        )

        # Stage 7-8: convert + install v0.3.
        v3_gguf = convert_to_gguf(tools_run_dir, "v0.3")
        install_live(v3_gguf, "v0.3")

        # Stage 9: report.
        write_final_report()
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}")
        update_report("error", {"type": type(e).__name__, "msg": str(e)})
        raise


if __name__ == "__main__":
    main()
