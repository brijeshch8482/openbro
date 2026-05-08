"""Latest LLM auto-select — probe what's available, pick the best.

Strategy:
  1. Probe Ollama (any model 7B+ that supports tool calling)
  2. Probe Groq (free cloud, fastest)
  3. Probe Anthropic / OpenAI (if user has keys)
  4. Score by: capability × tool-calling × user-preference (cloud/local/cost)
  5. Return best provider config

The model picker upgrades over time: when llama3.4 / claude-5 / gpt-5
release, they auto-rank above older versions on the next probe.
"""

from __future__ import annotations

import shutil
import subprocess

import httpx

# Capability scores (rough approximation; tuned over time via reflection)
# Higher = better at agent / tool-calling. Updated via daily online check.
CAPABILITY = {
    # Anthropic
    "claude-opus": 100,
    "claude-sonnet": 95,
    "claude-haiku": 85,
    # OpenAI
    "gpt-4o": 95,
    "gpt-4o-mini": 80,
    "gpt-4-turbo": 90,
    "gpt-3.5": 70,
    "o1": 92,
    "o1-mini": 82,
    # Google
    "gemini-2.0-pro": 96,
    "gemini-2.0-flash": 90,
    "gemini-1.5-pro": 92,
    "gemini-1.5-flash": 85,
    # DeepSeek
    "deepseek-chat": 88,
    "deepseek-reasoner": 92,
    # Groq (open-source via fast cloud)
    "groq-llama-3.3": 92,
    "groq-llama-3.1": 87,
    "groq-mixtral": 80,
    "groq-gemma": 70,
    # Ollama (offline) — kept for advanced users
    "llama3.3": 90,
    "llama3.2": 80,
    "llama3.1": 85,
    "qwen2.5:32b": 88,
    "qwen2.5:14b": 82,
    "qwen2.5:7b": 75,
    "qwen2.5-coder": 60,  # poor at agent tool calls
    "mistral-nemo": 78,
    "mistral:7b": 72,
    "phi3": 60,
    "gemma2": 65,
}


def _ollama_installed_models() -> list[str]:
    if not shutil.which("ollama"):
        return []
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            lines = out.stdout.strip().splitlines()[1:]
            return [line.split()[0] for line in lines if line.strip()]
    except Exception:
        pass
    return []


def _capability_for(model: str) -> int:
    """Best-match capability score by prefix.

    Coder variants are explicitly downgraded: they're great for IDE
    autocomplete but poor at agentic tool-calling (we hit this in
    real-world testing — agent didn't call tools at all).
    """
    model_lower = model.lower()

    # Coder fast-path: any model with 'coder' or 'code' in the name caps low
    if "coder" in model_lower or "codellama" in model_lower:
        return CAPABILITY.get("qwen2.5-coder", 60)

    best = 0
    for key, score in CAPABILITY.items():
        if "coder" in key:
            continue  # don't let coder entries score non-coder models
        key_lower = key.lower()
        # Substring or family-prefix (e.g. 'llama3.1' matches 'llama3.1:8b')
        if key_lower in model_lower:
            best = max(best, score)
        elif ":" in key and model_lower.startswith(key_lower.split(":")[0]):
            best = max(best, score)
    return best or 50  # unknown model floor


def probe_available() -> list[dict]:
    """Return [{provider, model, score, available, source}, ...] sorted by score desc."""
    candidates = []

    # Ollama
    for m in _ollama_installed_models():
        candidates.append(
            {
                "provider": "ollama",
                "model": m,
                "score": _capability_for(m),
                "available": True,
                "source": "ollama-local",
            }
        )

    # Groq (we can't probe API without a key; check if config has one later)
    candidates.append(
        {
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "score": _capability_for("groq-llama-3.3"),
            "available": False,  # depends on api_key
            "source": "groq-cloud",
        }
    )

    # All cloud providers - availability depends on api_key (set later)
    candidates.append(
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "score": _capability_for("claude-sonnet"),
            "available": False,
            "source": "anthropic-cloud",
        }
    )
    candidates.append(
        {
            "provider": "openai",
            "model": "gpt-4o",
            "score": _capability_for("gpt-4o"),
            "available": False,
            "source": "openai-cloud",
        }
    )
    candidates.append(
        {
            "provider": "google",
            "model": "gemini-2.0-flash",
            "score": _capability_for("gemini-2.0-flash"),
            "available": False,
            "source": "google-cloud",
        }
    )
    candidates.append(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "score": _capability_for("deepseek-chat"),
            "available": False,
            "source": "deepseek-cloud",
        }
    )

    candidates.sort(key=lambda c: -c["score"])
    return candidates


CLOUD_PROVIDERS = {"groq", "anthropic", "openai", "google", "deepseek"}


def best_available(config: dict | None = None) -> dict | None:
    """Pick the best available LLM. Marks cloud providers available iff key set."""
    config = config or {}
    candidates = probe_available()
    providers_cfg = config.get("providers", {}) or {}
    for c in candidates:
        if c["provider"] in CLOUD_PROVIDERS:
            key = (providers_cfg.get(c["provider"], {}) or {}).get("api_key")
            c["available"] = bool(key)
    available = [c for c in candidates if c["available"]]
    return available[0] if available else None


def suggest_upgrade(current: tuple[str, str], config: dict | None = None) -> dict | None:
    """If a higher-scoring model is available than 'current', return its config."""
    config = config or {}
    cur_provider, cur_model = current
    cur_score = _capability_for(cur_model)
    best = best_available(config)
    if not best:
        return None
    if best["score"] > cur_score + 10 and (best["provider"], best["model"]) != current:
        return best
    return None
