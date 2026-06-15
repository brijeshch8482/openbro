"""Adapter swap engine — one base model in VRAM, many LoRA adapters
hot-swapped per query.

Why this shape instead of separate GGUFs:
  * Loading a 700 MB GGUF from disk = 30-90 s; switching adapters on
    an already-loaded base = ~100 ms.
  * 4 GB VRAM only fits one base at a time anyway.
  * Disk footprint per specialist drops from ~700 MB → ~50 MB.

The engine is lazy: the base model + first adapter are loaded on the
first chat() call, and adapters are cached LRU-style so repeated
switches in a session are free after the first warm-up.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GenResult:
    """One chat-completion result."""

    text: str
    adapter_slug: str | None
    base_model: str
    elapsed_s: float


class AdapterEngine:
    """Loads base + swappable LoRA adapters.

    Keep a small LRU of attached adapters so back-to-back queries to
    the same category don't pay the swap cost twice.
    """

    def __init__(
        self,
        base_model: str = "HuggingFaceTB/SmolLM2-360M-Instruct",
        adapters_dir: str = "D:/OpenBro-teting/specialists/adapters",
        cache_size: int = 4,
    ) -> None:
        self.base_model = base_model
        self.adapters_dir = Path(adapters_dir)
        self.cache_size = cache_size
        self._model = None
        self._tokenizer = None
        self._active_slug: str | None = None
        # OrderedDict for LRU.
        self._adapter_cache: OrderedDict[str, str] = OrderedDict()

    # ─── Lazy setup ────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch  # noqa: PLC0415
        from transformers import (  # noqa: PLC0415
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
        )

        self._tokenizer = AutoTokenizer.from_pretrained(self.base_model)
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        bnb = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            self.base_model, quantization_config=bnb, device_map="auto"
        )

    # ─── Adapter swap ──────────────────────────────────────────────

    def attach(self, slug: str) -> bool:
        """Make the given slug the active adapter. Returns True if a
        specialist was found and attached; False if no adapter exists
        for the slug (caller can decide to fall back to base)."""
        self._ensure_loaded()
        adapter_path = self.adapters_dir / f"{slug}"
        if not adapter_path.exists():
            return False
        from peft import PeftModel  # noqa: PLC0415

        if slug == self._active_slug:
            return True
        if not isinstance(self._model, PeftModel) and slug in self._adapter_cache:
            # Should never hit (we always wrap on first attach), but
            # guarded for safety.
            return True
        if hasattr(self._model, "load_adapter"):
            # Subsequent attaches use the merge/swap API.
            self._model.load_adapter(str(adapter_path), adapter_name=slug)
            self._model.set_adapter(slug)
        else:
            # First attach: wrap base in a PeftModel and load the adapter.
            self._model = PeftModel.from_pretrained(
                self._model, str(adapter_path), adapter_name=slug
            )
        self._active_slug = slug
        # LRU bookkeeping.
        self._adapter_cache[slug] = str(adapter_path)
        self._adapter_cache.move_to_end(slug)
        while len(self._adapter_cache) > self.cache_size:
            evicted, _ = self._adapter_cache.popitem(last=False)
            if hasattr(self._model, "delete_adapter") and evicted != slug:
                try:
                    self._model.delete_adapter(evicted)
                except Exception:
                    pass
        return True

    def detach(self) -> None:
        """Drop back to the base model (no specialist active)."""
        if self._model is None or self._active_slug is None:
            return
        if hasattr(self._model, "disable_adapter"):
            # PEFT's context manager / explicit disable — just unset
            # the active adapter name so generation uses base weights.
            self._model.set_adapter(None)  # type: ignore[arg-type]
        self._active_slug = None

    # ─── Generation ────────────────────────────────────────────────

    def chat(
        self,
        prompt: str,
        slug: str | None = None,
        max_new_tokens: int = 256,
        temperature: float = 0.2,
    ) -> GenResult:
        """Generate a response for `prompt`. If `slug` is given and a
        matching adapter exists, it is attached; otherwise generation
        runs on the bare base model."""
        import time as _time  # noqa: PLC0415

        import torch  # noqa: PLC0415

        self._ensure_loaded()
        attached = False
        if slug:
            attached = self.attach(slug)
        if not attached:
            self.detach()

        msgs = [{"role": "user", "content": prompt}]
        chat_text = self._tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True
        )
        inputs = self._tokenizer(chat_text, return_tensors="pt").to(self._model.device)

        t0 = _time.time()
        with torch.no_grad():
            out_ids = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                do_sample=temperature > 0,
                pad_token_id=self._tokenizer.pad_token_id,
            )
        elapsed = _time.time() - t0
        gen_tokens = out_ids[0][inputs.input_ids.shape[1] :]
        text = self._tokenizer.decode(gen_tokens, skip_special_tokens=True).strip()

        return GenResult(
            text=text,
            adapter_slug=slug if attached else None,
            base_model=self.base_model,
            elapsed_s=elapsed,
        )

    # ─── Misc ──────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        return {
            "base_model": self.base_model,
            "loaded": self._model is not None,
            "active_adapter": self._active_slug,
            "cached_adapters": list(self._adapter_cache.keys()),
        }
