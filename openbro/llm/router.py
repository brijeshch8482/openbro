"""LLM provider router - picks the right provider based on config."""

import sys

from openbro.llm.base import LLMProvider
from openbro.utils.config import load_config


def create_provider(provider_name: str | None = None) -> LLMProvider:
    """Create the LLM provider, optionally wrapped with a fallback.

    Config knobs:
      llm.provider   - primary provider name (groq / anthropic / local / …)
      llm.fallback   - OPTIONAL fallback provider name. When set, the
                       returned provider is a FallbackProvider that
                       transparently cascades on recoverable errors.

    Building the fallback is best-effort: if the user has fallback=local
    configured but no model downloaded yet (first launch, download in
    progress), we log a warning and return the primary alone. The
    download flow re-runs setup once it completes so the fallback is
    available on the next request.
    """
    config = load_config()

    if provider_name is None:
        provider_name = config["llm"]["provider"]

    providers_config = config.get("providers", {})
    primary = _build_one(provider_name, config, providers_config)

    # Auto-fallback chain: only wraps when the user explicitly sets it
    # AND it's not the same provider. Default config leaves this empty
    # so existing single-provider users see no behavior change.
    fallback_name = (config.get("llm", {}) or {}).get("fallback")
    if (
        fallback_name
        and isinstance(fallback_name, str)
        and fallback_name.strip()
        and fallback_name != provider_name
    ):
        try:
            fallback = _build_one(fallback_name, config, providers_config)
        except Exception as e:
            # Don't fail-fast — primary still works. Tell the user once
            # so they know the cushion isn't active yet.
            print(
                f"[fallback] '{fallback_name}' not available yet ({e}). "
                "Primary will run alone; fallback will activate when ready.",
                file=sys.stderr,
            )
            return primary
        from openbro.llm.fallback_provider import FallbackProvider

        return FallbackProvider(primary=primary, fallback=fallback)

    return primary


def _build_one(provider_name: str, config: dict, providers_config: dict) -> LLMProvider:
    """Build a single provider (no wrapping). Same logic as the original
    router; pulled into a helper so the fallback wrapper can call it
    twice with different names."""

    if provider_name in ("local", "ollama"):
        # 'ollama' kept as alias for back-compat with old configs; both now
        # route to the in-process llama.cpp engine (no external daemon).
        from openbro.llm.local_provider import LocalLLMProvider
        from openbro.utils.local_llm_setup import DEFAULT_MODEL, find_installed_match

        local_cfg = providers_config.get("local") or providers_config.get("ollama") or {}
        # Captured failure: when 'local' is the FALLBACK and primary is a
        # different cloud provider, config['llm']['model'] holds the cloud
        # model name (e.g. 'meta-llama/llama-4-scout-...'). Looking that
        # up in the local GGUF catalogue obviously fails. The local
        # provider's own model name lives on providers.local.model — use
        # that first; only fall through to llm.model when the user
        # explicitly chose local as their primary.
        model_name = (
            local_cfg.get("model")
            or (
                config["llm"].get("model")
                if config.get("llm", {}).get("provider") == "local"
                else None
            )
            or DEFAULT_MODEL
        )
        model_path = local_cfg.get("model_path")
        if not model_path:
            p = find_installed_match(model_name)
            if not p:
                raise ValueError(
                    f"No local model found for '{model_name}'. "
                    "Download one with:\n"
                    "  openbro model download llama3.1:8b\n"
                    "Or import a GGUF file you already have:\n"
                    "  openbro model import D:/path/to/model.gguf"
                )
            model_path = str(p)
        return LocalLLMProvider(
            model_path=model_path,
            model_name=model_name,
            n_ctx=local_cfg.get("n_ctx", 16384),
            n_gpu_layers=local_cfg.get("n_gpu_layers", -1),
        )

    elif provider_name == "anthropic":
        from openbro.llm.anthropic_provider import AnthropicProvider

        anthropic_cfg = providers_config.get("anthropic", {})
        api_key = anthropic_cfg.get("api_key")
        if not api_key:
            raise ValueError(
                "Anthropic API key not set. Run: openbro"
                " config set providers.anthropic.api_key YOUR_KEY"
            )
        return AnthropicProvider(
            api_key=api_key,
            model=anthropic_cfg.get("model", "claude-sonnet-4-20250514"),
        )

    elif provider_name == "openai":
        from openbro.llm.openai_provider import OpenAIProvider

        openai_cfg = providers_config.get("openai", {})
        api_key = openai_cfg.get("api_key")
        if not api_key:
            raise ValueError(
                "OpenAI API key not set. Run: openbro config set providers.openai.api_key YOUR_KEY"
            )
        return OpenAIProvider(
            api_key=api_key,
            model=openai_cfg.get("model", "gpt-4o"),
        )

    elif provider_name == "groq":
        from openbro.llm.groq_provider import GroqProvider

        groq_cfg = providers_config.get("groq", {})
        api_key = groq_cfg.get("api_key")
        if not api_key:
            raise ValueError(
                "Groq API key not set. Run: openbro config set providers.groq.api_key YOUR_KEY"
            )
        return GroqProvider(
            api_key=api_key,
            model=groq_cfg.get("model", "meta-llama/llama-4-scout-17b-16e-instruct"),
        )

    elif provider_name == "google":
        from openbro.llm.google_provider import GoogleProvider

        google_cfg = providers_config.get("google", {})
        api_key = google_cfg.get("api_key")
        if not api_key:
            raise ValueError(
                "Google API key not set. Get one free at https://aistudio.google.com/apikey"
            )
        return GoogleProvider(
            api_key=api_key,
            model=google_cfg.get("model", "gemini-1.5-flash"),
        )

    elif provider_name == "deepseek":
        from openbro.llm.deepseek_provider import DeepSeekProvider

        ds_cfg = providers_config.get("deepseek", {})
        api_key = ds_cfg.get("api_key")
        if not api_key:
            raise ValueError("DeepSeek API key not set. Get one at https://platform.deepseek.com")
        return DeepSeekProvider(
            api_key=api_key,
            model=ds_cfg.get("model", "deepseek-chat"),
        )

    else:
        raise ValueError(
            f"Unknown provider: {provider_name}. "
            "Available: local, anthropic, openai, groq, google, deepseek"
        )
