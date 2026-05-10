"""LLM provider router - picks the right provider based on config."""

from openbro.llm.base import LLMProvider
from openbro.utils.config import load_config


def create_provider(provider_name: str | None = None) -> LLMProvider:
    """Create an LLM provider based on config or explicit name."""
    config = load_config()

    if provider_name is None:
        provider_name = config["llm"]["provider"]

    providers_config = config.get("providers", {})

    if provider_name in ("local", "ollama"):
        # 'ollama' kept as alias for back-compat with old configs; both now
        # route to the in-process llama.cpp engine (no external daemon).
        from openbro.llm.local_provider import LocalLLMProvider
        from openbro.utils.local_llm_setup import find_installed_match

        local_cfg = providers_config.get("local") or providers_config.get("ollama") or {}
        model_name = config["llm"].get("model", "llama3.1:8b")
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
            n_ctx=local_cfg.get("n_ctx", 8192),
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
            model=groq_cfg.get("model", "llama-3.3-70b-versatile"),
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
