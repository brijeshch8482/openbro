"""Model add/remove/switch - one command for any model, online or offline.

Knows about both Ollama (offline, downloads + uninstalls) and cloud
providers (anthropic/openai/groq - just stores or clears API keys).
"""

from __future__ import annotations

import subprocess

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from openbro.utils.config import load_config, save_config

console = Console()

CLOUD_PROVIDERS = {"anthropic", "openai", "groq"}
OFFLINE_PROVIDERS = {"ollama"}

# Friendly aliases → (provider, model_name)
ALIASES = {
    "claude": ("anthropic", "claude-sonnet-4-20250514"),
    "claude-opus": ("anthropic", "claude-opus-4-20250514"),
    "gpt": ("openai", "gpt-4o"),
    "gpt-4o": ("openai", "gpt-4o"),
    "gpt-mini": ("openai", "gpt-4o-mini"),
    "groq": ("groq", "llama-3.3-70b-versatile"),
    "llama": ("ollama", "llama3.2:3b"),
    "qwen": ("ollama", "qwen2.5-coder:7b"),
    "mistral": ("ollama", "mistral:7b"),
    "gemma": ("ollama", "gemma2:2b"),
}


def _resolve(name: str) -> tuple[str, str]:
    """Map shortcut → (provider, model)."""
    name = name.strip().lower()
    if name in ALIASES:
        return ALIASES[name]
    if name in CLOUD_PROVIDERS or name in OFFLINE_PROVIDERS:
        cfg = load_config()
        model = cfg.get("providers", {}).get(name, {}).get("model", "")
        return name, model
    if ":" in name or "/" in name:
        return "ollama", name
    if name.startswith("claude"):
        return "anthropic", name
    if name.startswith("gpt") or name.startswith("o1"):
        return "openai", name
    return "ollama", name


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

    for alias, (prov, model) in ALIASES.items():
        is_active = (prov, model) == current
        status = "[green]✓ active[/green]" if is_active else ""
        if prov in CLOUD_PROVIDERS:
            has_key = bool(cfg.get("providers", {}).get(prov, {}).get("api_key"))
            status += " [dim](key set)[/dim]" if has_key else " [dim](no key)[/dim]"
        table.add_row(alias, prov, model, status)
    console.print(table)
    console.print(
        "\n[dim]Use 'model add <alias>' to add, "
        "'model switch <alias>' to switch, 'model remove <alias>' to delete offline.[/dim]\n"
    )


def add_model(name: str) -> bool:
    """Install / configure a model. Single command for online or offline."""
    provider, model = _resolve(name)
    cfg = load_config()

    if provider == "ollama":
        from openbro.utils.ollama_setup import (
            is_ollama_installed,
            is_ollama_running,
            pull_model,
            start_ollama_server,
        )

        if not is_ollama_installed():
            console.print("[red]Ollama not installed. Run 'openbro --setup' first.[/red]")
            return False
        if not is_ollama_running():
            console.print("[dim]Starting Ollama server...[/dim]")
            start_ollama_server()
        console.print(f"[cyan]Downloading {model}...[/cyan]")
        ok = pull_model(model)
        if ok:
            console.print(f"[green]✓ Added offline model: {model}[/green]")
        return ok

    # Cloud provider - just take API key if missing
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
    """Uninstall offline model OR clear cloud API key."""
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

    if provider == "ollama":
        try:
            result = subprocess.run(
                ["ollama", "rm", model],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                console.print(f"[red]Failed: {result.stderr.strip()}[/red]")
                return False
            console.print(f"[green]✓ Removed offline model: {model}[/green]")
            return True
        except FileNotFoundError:
            console.print("[red]Ollama not found.[/red]")
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
    """Switch active model. If switching offline → optionally remove old offline model."""
    new_provider, new_model = _resolve(name)
    cfg = load_config()
    old_provider = cfg.get("llm", {}).get("provider")
    old_model = cfg.get("llm", {}).get("model")

    # Make sure target is available
    if new_provider == "ollama":
        from openbro.utils.ollama_setup import get_installed_models, is_ollama_running

        if not is_ollama_running():
            console.print("[yellow]Ollama not running. Starting...[/yellow]")
            from openbro.utils.ollama_setup import start_ollama_server

            start_ollama_server()
        installed = get_installed_models()
        if new_model not in installed:
            if Confirm.ask(f"{new_model} downloaded nahi hai. Abhi download karu?", default=True):
                if not add_model(name):
                    return False
            else:
                return False
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

    # Offer to remove old offline model
    if prompt_remove_old and old_provider == "ollama" and old_model and old_model != new_model:
        from openbro.utils.ollama_setup import get_installed_models

        installed = get_installed_models() if old_provider == "ollama" else []
        if old_model in installed:
            if Confirm.ask(
                f"Purana offline model '{old_model}' laptop pe hai. Disk free karne "
                f"ke liye remove karu?",
                default=False,
            ):
                try:
                    subprocess.run(["ollama", "rm", old_model], check=False)
                    console.print(f"[green]✓ Removed old model: {old_model}[/green]")
                except Exception as e:
                    console.print(f"[yellow]Could not remove: {e}[/yellow]")

    return True
