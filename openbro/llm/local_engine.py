"""Local LLM inference engine — wraps llama-cpp-python (Apache 2.0 llama.cpp).

Why this exists: we run offline LLMs entirely in-process. No external daemon
(no `ollama serve`), no HTTP hop, no separate install. Pure Python → C++ →
tokens, same engine that Ollama / LM Studio / Jan use under the hood.

Models are GGUF files on disk; download them via huggingface_hub or import
manually (e.g. transferred via USB on an air-gapped machine).

Public API:
    engine = LocalEngine(model_path="D:/models/llama-3.1-8b.gguf")
    out    = engine.chat(messages)            # OpenAI-style dict
    for tok in engine.stream(messages):       # token-by-token
        print(tok, end="", flush=True)
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

DEFAULT_CTX = 8192
DEFAULT_GPU_LAYERS = -1  # -1 = offload everything that fits to GPU; 0 = CPU only

DEPS_HINT = (
    "llama-cpp-python is not installed. Run:\n"
    "  pip install 'openbro[local]'\n"
    "(Wheels exist for Python 3.10-3.13 on Windows/Mac/Linux. If yours is "
    "newer, the installer auto-uses Python 3.12.)"
)


class LocalEngine:
    """Lazy-loaded llama.cpp engine. Model isn't actually loaded into RAM
    until the first chat() / stream() call — keeps imports cheap."""

    def __init__(
        self,
        model_path: str | Path,
        n_ctx: int = DEFAULT_CTX,
        n_gpu_layers: int = DEFAULT_GPU_LAYERS,
        chat_format: str | None = None,
    ):
        self.model_path = Path(model_path)
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.chat_format = chat_format
        self._llm = None

    def _load(self) -> None:
        if self._llm is not None:
            return
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Model file not found: {self.model_path}\n"
                "Download one with: openbro model download llama3.1:8b"
            )
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise RuntimeError(DEPS_HINT) from e

        kwargs: dict = {
            "model_path": str(self.model_path),
            "n_ctx": self.n_ctx,
            "n_gpu_layers": self.n_gpu_layers,
            "verbose": False,
        }
        if self.chat_format:
            kwargs["chat_format"] = self.chat_format
        self._llm = Llama(**kwargs)

    # ─── public API ───────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
    ) -> dict:
        """Returns OpenAI-style chat completion dict (with choices[0].message)."""
        self._load()
        kwargs: dict = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return self._llm.create_chat_completion(**kwargs)

    def stream(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> Iterator[str]:
        """Yield assistant content token-by-token."""
        self._load()
        for chunk in self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        ):
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield content

    def unload(self) -> None:
        """Free the model from RAM. Useful before switching models."""
        self._llm = None
        import gc

        gc.collect()

    @property
    def loaded(self) -> bool:
        return self._llm is not None
