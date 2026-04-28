"""First-run wizard - interactive setup on first launch."""

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from openbro.utils.config import get_config_path, save_config, default_config

console = Console()

BANNER = r"""
   ____                   ____
  / __ \____  ___  ____  / __ )_________
 / / / / __ \/ _ \/ __ \/ __  / ___/ __ \
/ /_/ / /_/ /  __/ / / / /_/ / /  / /_/ /
\____/ .___/\___/_/ /_/_____/_/   \____/
    /_/
"""


def needs_setup() -> bool:
    return not get_config_path().exists()


def run_wizard():
    console.print(Panel(BANNER, title="[bold cyan]Welcome to OpenBro![/bold cyan]", border_style="cyan"))
    console.print("[bold]Tera Apna AI Bro - Open-Source Personal AI Agent[/bold]")
    console.print("[dim]Let's set you up in under 2 minutes.\n[/dim]")

    config = default_config()

    # Step 1: Choose LLM provider
    console.print("[bold yellow]Step 1:[/bold yellow] Choose your LLM provider\n")
    console.print("  [cyan]1.[/cyan] Ollama (offline, free, local) [green]<-- recommended[/green]")
    console.print("  [cyan]2.[/cyan] Groq (cloud, free tier, ultra-fast)")
    console.print("  [cyan]3.[/cyan] Anthropic (Claude API, paid)")
    console.print("  [cyan]4.[/cyan] OpenAI (GPT API, paid)")
    console.print()

    choice = Prompt.ask("Select provider", choices=["1", "2", "3", "4"], default="1")

    if choice == "1":
        config["llm"]["provider"] = "ollama"
        model = Prompt.ask(
            "Ollama model",
            default="qwen2.5-coder:7b",
        )
        config["llm"]["model"] = model
        console.print(f"[green]Ollama selected with model: {model}[/green]")
        console.print("[dim]Make sure Ollama is running: ollama serve[/dim]\n")

    elif choice == "2":
        config["llm"]["provider"] = "groq"
        api_key = Prompt.ask("Groq API key (free at console.groq.com)")
        config["providers"]["groq"]["api_key"] = api_key
        model = Prompt.ask("Model", default="llama-3.3-70b-versatile")
        config["providers"]["groq"]["model"] = model
        config["llm"]["model"] = model
        console.print(f"[green]Groq selected with model: {model}[/green]\n")

    elif choice == "3":
        config["llm"]["provider"] = "anthropic"
        api_key = Prompt.ask("Anthropic API key")
        config["providers"]["anthropic"]["api_key"] = api_key
        model = Prompt.ask("Model", default="claude-sonnet-4-20250514")
        config["providers"]["anthropic"]["model"] = model
        config["llm"]["model"] = model
        console.print(f"[green]Anthropic selected with model: {model}[/green]\n")

    elif choice == "4":
        config["llm"]["provider"] = "openai"
        api_key = Prompt.ask("OpenAI API key")
        config["providers"]["openai"]["api_key"] = api_key
        model = Prompt.ask("Model", default="gpt-4o")
        config["providers"]["openai"]["model"] = model
        config["llm"]["model"] = model
        console.print(f"[green]OpenAI selected with model: {model}[/green]\n")

    # Step 2: Safety settings
    console.print("[bold yellow]Step 2:[/bold yellow] Safety settings\n")
    confirm_dangerous = Confirm.ask(
        "Confirm before running dangerous commands?",
        default=True,
    )
    config["safety"]["confirm_dangerous"] = confirm_dangerous

    # Step 3: Personality
    console.print("\n[bold yellow]Step 3:[/bold yellow] Personality\n")
    console.print("  [cyan]1.[/cyan] Hinglish Bro (default - Hindi+English mix)")
    console.print("  [cyan]2.[/cyan] English Professional")
    console.print("  [cyan]3.[/cyan] Hindi")
    console.print()

    personality = Prompt.ask("Select personality", choices=["1", "2", "3"], default="1")

    prompts = {
        "1": "Tu OpenBro hai - ek helpful AI bro. Friendly aur casual reh, Hindi-English mix me baat kar. User ki help kar. Short aur to-the-point answers de.",
        "2": "You are OpenBro, a helpful AI assistant. Be professional, clear, and concise. Help the user with their tasks efficiently.",
        "3": "Tu OpenBro hai - ek helpful AI assistant. Hindi me baat kar. User ki help kar. Short aur clear answers de.",
    }
    config["agent"]["system_prompt"] = prompts[personality]

    # Save config
    save_config(config)

    console.print("\n[bold green]Setup complete![/bold green]")
    console.print(f"[dim]Config saved to: {get_config_path()}[/dim]")
    console.print("\n[bold cyan]Type anything to start chatting with your AI Bro![/bold cyan]\n")
