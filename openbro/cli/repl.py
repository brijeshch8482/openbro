"""Interactive REPL for OpenBro."""

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown

from openbro import __version__
from openbro.core.agent import Agent
from openbro.utils.config import get_config_dir

console = Console()


def print_banner():
    console.print(f"\n[bold cyan]OpenBro v{__version__}[/bold cyan] - Tera Apna AI Bro")
    console.print("[dim]Type 'exit' or 'quit' to leave. Type 'help' for commands.[/dim]\n")


def start_repl():
    print_banner()

    config_dir = get_config_dir()
    history_file = config_dir / "history.txt"
    session = PromptSession(history=FileHistory(str(history_file)))

    agent = Agent()

    while True:
        try:
            user_input = session.prompt("You > ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "bye"):
                console.print("[bold cyan]Chal bhai, phir milte hai![/bold cyan]")
                break

            if user_input.lower() == "help":
                _show_help()
                continue

            response = agent.chat(user_input)
            console.print(f"\n[bold green]Bro:[/bold green] {response}\n")

        except KeyboardInterrupt:
            console.print("\n[bold cyan]Ctrl+C? Chal theek hai, phir milte hai![/bold cyan]")
            break
        except EOFError:
            break


def _show_help():
    help_text = """
## Commands
- `help` - Show this help
- `exit` / `quit` - Exit OpenBro
- `config` - Show current configuration
- `model` - Show current LLM model

Just type naturally and OpenBro will help you!
"""
    console.print(Markdown(help_text))
