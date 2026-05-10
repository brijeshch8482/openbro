"""Model add/remove/switch — one command for any model, online or offline.

Knows about both local LLMs (downloads/imports/deletes GGUF files) and cloud
providers (anthropic/openai/groq/google/deepseek — just stores or clears API keys).
"""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from openbro.utils.config import load_config, save_config

console = Console()

CLOUD_PROVIDERS = {"anthropic", "openai", "groq", "google", "deepseek"}
LOCAL_PROVIDERS = {"local"}

# Friendly aliases → (provider, model_name).
# Local aliases use the registry keys from openbro.utils.local_llm_setup.MODELS
ALIASES = {
    "claude": ("anthropic", "claude-sonnet-4-20250514"),
    "claude-opus": ("anthropic", "claude-opus-4-20250514"),
    "gpt": ("openai", "gpt-4o"),
    "gpt-4o": ("openai", "gpt-4o"),
    "gpt-mini": ("openai", "gpt-4o-mini"),
    "groq": ("groq", "llama-3.3-70b-versatile"),
    "gemini": ("google", "gemini-1.5-flash"),
    "deepseek": ("deepseek", "deepseek-chat"),
    "llama": ("local", "llama3.1:8b"),
    "llama-small": ("local", "llama3.2:3b"),
    "llama-tiny": ("local", "llama3.2:1b"),
    "mistral": ("local", "mistral:7b"),
    "mistral-nemo": ("local", "mistral-nemo"),
    "codestral": ("local", "codestral:22b"),
    "phi": ("local", "phi3:mini"),
    "gemma": ("local", "gemma2:2b"),
}


def _resolve(name: str) -> tuple[str, str]:
    """Map shortcut → (provider, model)."""
    name = name.strip().lower()
    if name in ALIASES:
        return ALIASES[name]
    if name in CLOUD_PROVIDERS or name in LOCAL_PROVIDERS:
        cfg = load_config()
        model = cfg.get("providers", {}).get(name, {}).get("model", "")
        return name, model
    if name == "ollama":  # back-compat alias
        return "local", "llama3.1:8b"
    if name.startswith("claude"):
        return "anthropic", name
    if name.startswith("gpt") or name.startswith("o1"):
        return "openai", name
    if name.startswith("gemini"):
        return "google", name
    # Anything else with a colon (registry-style) treated as a local model
    if ":" in name:
        return "local", name
    return "local", name


def list_available() -> None:
    cfg = load_config()
    table = Table(title="Models", border_style="cyan")
    table.add_column("Alias", style="bold")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Status", justify="center")

    current = (
        cfg.get("llm", {}).get("provider", ""),
        cfg.get("llm", {}).get("model", ""),
    )

    from openbro.utils.local_llm_setup import find_installed_match

    for alias, (prov, model) in ALIASES.items():
        is_active = (prov, model) == current
        status = "[green]✓ active[/green]" if is_active else ""
        if prov in CLOUD_PROVIDERS:
            has_key = bool(cfg.get("providers", {}).get(prov, {}).get("api_key"))
            status += " [dim](key set)[/dim]" if has_key else " [dim](no key)[/dim]"
        elif prov == "local":
            on_disk = find_installed_match(model) is not None
            status += " [dim](downloaded)[/dim]" if on_disk else " [dim](not downloaded)[/dim]"
        table.add_row(alias, prov, model, status)
    console.print(table)
    console.print(
        "\n[dim]Use 'model add <alias>' to add, "
        "'model switch <alias>' to switch, 'model remove <alias>' to delete.[/dim]\n"
    )


def add_model(name: str) -> bool:
    """Install / configure a model. Single command for online or offline."""
    provider, model = _resolve(name)
    cfg = load_config()

    if provider == "local":
        from openbro.utils.local_llm_setup import (
            MODELS,
            download_model,
            ensure_llama_cpp_python,
        )

        if model not in MODELS:
            console.print(
                f"[red]Unknown local model: {model}[/red]\n"
                f"[dim]Available: {', '.join(MODELS.keys())}[/dim]"
            )
            return False
        if not ensure_llama_cpp_python():
            return False
        path = download_model(model)
        if path:
            cfg.setdefault("providers", {}).setdefault("local", {})
            cfg["providers"]["local"]["model_path"] = str(path)
            cfg["providers"]["local"]["model"] = model
            save_config(cfg)
            console.print(f"[green]✓ Added local model: {model}[/green]")
            return True
        return False

    # Cloud provider — just take API key if missing
    cur_key = cfg.get("providers", {}).get(provider, {}).get("api_key")
    if not cur_key:
        key = Prompt.ask(f"Paste your {provider} API key", password=True)
        if not key:
            console.print("[yellow]Skipped.[/yellow]")
            return False
        cfg.setdefault("providers", {}).setdefault(provider, {})["api_key"] = key
        cfg["providers"][provider]["model"] = model
        save_config(cfg)
        console.print(f"[green]✓ Added cloud model: {provider} / {model}[/green]")
    else:
        cfg["providers"][provider]["model"] = model
        save_config(cfg)
        console.print(f"[green]✓ Updated to: {provider} / {model}[/green]")
    return True


def remove_model(name: str) -> bool:
    """Delete a local GGUF file OR clear a cloud API key."""
    provider, model = _resolve(name)
    cfg = load_config()

    current = (
        cfg.get("llm", {}).get("provider"),
        cfg.get("llm", {}).get("model"),
    )

    if (provider, model) == current:
        if not Confirm.ask(
            f"Yeh tera active model hai ({provider}/{model}). Remove karna hai?",
            default=False,
        ):
            return False

    if provider == "local":
        from openbro.utils.local_llm_setup import find_installed_match

        path = find_installed_match(model)
        if not path:
            console.print(f"[yellow]Model not on disk: {model}[/yellow]")
            return False
        try:
            path.unlink()
            console.print(f"[green]✓ Removed local model file: {path.name}[/green]")
            return True
        except OSError as e:
            console.print(f"[red]Failed: {e}[/red]")
            return False

    # Cloud: clear API key
    if Confirm.ask(f"Clear {provider} API key from config?", default=False):
        if provider in cfg.get("providers", {}):
            cfg["providers"][provider]["api_key"] = None
            save_config(cfg)
            console.print(f"[green]✓ Cleared {provider} API key.[/green]")
        return True
    return False


def switch_model(name: str, prompt_remove_old: bool = True) -> bool:
    """Switch active model. If switching local → optionally remove old file."""
    new_provider, new_model = _resolve(name)
    cfg = load_config()
    old_provider = cfg.get("llm", {}).get("provider")
    old_model = cfg.get("llm", {}).get("model")

    # Make sure target is available
    if new_provider == "local":
        from openbro.utils.local_llm_setup import find_installed_match

        path = find_installed_match(new_model)
        if not path:
            if Confirm.ask(f"{new_model} not downloaded. Download abhi karu?", default=True):
                if not add_model(name):
                    return False
                path = find_installed_match(new_model)
            else:
                return False
        cfg.setdefault("providers", {}).setdefault("local", {})
        cfg["providers"]["local"]["model_path"] = str(path) if path else None
    elif new_provider in CLOUD_PROVIDERS:
        if not cfg.get("providers", {}).get(new_provider, {}).get("api_key"):
            if not add_model(name):
                return False
            cfg = load_config()  # reload after add

    cfg.setdefault("llm", {})["provider"] = new_provider
    cfg["llm"]["model"] = new_model
    cfg.setdefault("providers", {}).setdefault(new_provider, {})["model"] = new_model
    save_config(cfg)
    console.print(f"[green]✓ Switched to: {new_provider} / {new_model}[/green]")

    # Offer to remove old local model file to free disk
    if (
        prompt_remove_old
        and old_provider in ("local", "ollama")
        and old_model
        and old_model != new_model
    ):
        from openbro.utils.local_llm_setup import find_installed_match

        old_path = find_installed_match(old_model)
        if old_path and old_path.exists():
            size_gb = old_path.stat().st_size / 1e9
            if Confirm.ask(
                f"Purana model '{old_model}' ({size_gb:.1f} GB) disk pe hai. "
                "Free karne ke liye delete karu?",
                default=False,
            ):
                try:
                    old_path.unlink()
                    console.print(f"[green]✓ Removed old model: {old_path.name}[/green]")
                except OSError as e:
                    console.print(f"[yellow]Could not remove: {e}[/yellow]")

    return True
