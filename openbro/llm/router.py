"""LLM provider router - picks the right provider based on config."""

from openbro.llm.base import LLMProvider
from openbro.utils.config import load_config


def create_provider(provider_name: str | None = None) -> LLMProvider:
    """Create an LLM provider based on config or explicit name."""
    config = load_config()

    if provider_name is None:
        provider_name = config["llm"]["provider"]

    providers_config = config.get("providers", {})

    if provider_name == "ollama":
        from openbro.llm.ollama_provider import OllamaProvider

        ollama_cfg = providers_config.get("ollama", {})
        return OllamaProvider(
            base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
            model=config["llm"].get("model", "qwen2.5-coder:7b"),
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
            "Available: anthropic, openai, groq, google, deepseek, ollama"
        )
