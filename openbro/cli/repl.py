"""Interactive REPL for OpenBro."""

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from openbro import __version__
from openbro.cli.wizard import needs_setup, run_wizard
from openbro.core.agent import Agent
from openbro.utils.config import get_config_dir, load_config, save_config

console = Console()

COMMANDS = ["help", "exit", "quit", "config", "model", "tools", "history", "clear", "reset"]
completer = WordCompleter(COMMANDS, ignore_case=True)


def print_banner():
    banner = f"[bold cyan]OpenBro v{__version__}[/bold cyan] - Tera Apna AI Bro"
    console.print(Panel(banner, border_style="cyan"))
    console.print("[dim]Type 'help' for commands. Just type naturally to chat![/dim]\n")


def start_repl():
    # First-run wizard
    if needs_setup():
        run_wizard()

    print_banner()

    config_dir = get_config_dir()
    history_file = config_dir / "history.txt"
    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
        complete_while_typing=False,
    )

    agent = Agent()

    while True:
        try:
            user_input = session.prompt("You > ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "bye"):
                console.print("[bold cyan]Chal bhai, phir milte hai![/bold cyan]")
                break

            # Handle built-in commands
            if _handle_command(user_input, agent):
                continue

            # Chat with agent - use streaming for real-time output
            console.print(f"\n[bold green]Bro:[/bold green] ", end="")
            response = ""
            try:
                for token in agent.stream_chat(user_input):
                    console.print(token, end="", highlight=False)
                    response += token
            except Exception:
                # Fallback to non-streaming
                with console.status("[dim]Bro soch raha hai...[/dim]", spinner="dots"):
                    response = agent.chat(user_input)
                console.print(response, highlight=False)
            console.print("\n")

        except KeyboardInterrupt:
            console.print("\n[bold cyan]Ctrl+C? Chal theek hai, phir milte hai![/bold cyan]")
            break
        except EOFError:
            break


def _handle_command(cmd: str, agent: Agent) -> bool:
    """Handle built-in commands. Returns True if command was handled."""
    cmd_lower = cmd.lower().strip()

    if cmd_lower == "help":
        _show_help()
        return True

    if cmd_lower == "config":
        _show_config()
        return True

    if cmd_lower.startswith("config set "):
        _set_config(cmd[11:].strip())
        return True

    if cmd_lower == "model":
        _show_model(agent)
        return True

    if cmd_lower.startswith("model "):
        _switch_model(cmd[6:].strip(), agent)
        return True

    if cmd_lower == "tools":
        _show_tools(agent)
        return True

    if cmd_lower == "clear":
        console.clear()
        print_banner()
        return True

    if cmd_lower == "reset":
        agent.history = [agent.history[0]]  # Keep system prompt only
        console.print("[yellow]Chat history cleared.[/yellow]\n")
        return True

    return False


def _show_help():
    table = Table(title="OpenBro Commands", border_style="cyan")
    table.add_column("Command", style="bold cyan")
    table.add_column("Description")

    table.add_row("help", "Show this help")
    table.add_row("config", "Show current configuration")
    table.add_row("config set <key> <val>", "Update config (e.g. config set llm.provider openai)")
    table.add_row("model", "Show current LLM model")
    table.add_row("model <name>", "Switch model (e.g. model gpt-4o)")
    table.add_row("tools", "List available tools")
    table.add_row("clear", "Clear screen")
    table.add_row("reset", "Clear chat history")
    table.add_row("exit / quit", "Exit OpenBro")

    console.print(table)
    console.print("\n[dim]Or just type naturally to chat with your AI Bro![/dim]\n")


def _show_config():
    config = load_config()
    import yaml
    config_str = yaml.dump(config, default_flow_style=False)
    console.print(Syntax(config_str, "yaml", theme="monokai"))


def _set_config(args: str):
    parts = args.split(maxsplit=1)
    if len(parts) != 2:
        console.print("[red]Usage: config set <key.path> <value>[/red]")
        console.print("[dim]Example: config set llm.provider anthropic[/dim]")
        return

    key_path, value = parts
    config = load_config()
    keys = key_path.split(".")

    # Navigate to the right nested dict
    obj = config
    for k in keys[:-1]:
        if k not in obj or not isinstance(obj[k], dict):
            obj[k] = {}
        obj = obj[k]

    # Convert value types
    if value.lower() == "true":
        value = True
    elif value.lower() == "false":
        value = False
    elif value.isdigit():
        value = int(value)

    obj[keys[-1]] = value
    save_config(config)
    console.print(f"[green]Set {key_path} = {value}[/green]\n")


def _show_model(agent: Agent):
    console.print(f"[cyan]Current model:[/cyan] {agent.provider.name()}\n")


def _switch_model(model_name: str, agent: Agent):
    config = load_config()

    # Check if it's a provider switch (e.g. "anthropic" or "openai")
    if model_name in ("ollama", "anthropic", "openai", "groq"):
        config["llm"]["provider"] = model_name
        save_config(config)
        agent.provider = __import__(
            "openbro.llm.router", fromlist=["create_provider"]
        ).create_provider(model_name)
        console.print(f"[green]Switched to provider: {model_name} ({agent.provider.name()})[/green]\n")
    else:
        # Just change the model name
        config["llm"]["model"] = model_name
        save_config(config)
        from openbro.llm.router import create_provider
        agent.provider = create_provider()
        console.print(f"[green]Switched model to: {agent.provider.name()}[/green]\n")


def _show_tools(agent: Agent):
    table = Table(title="Available Tools", border_style="cyan")
    table.add_column("Tool", style="bold")
    table.add_column("Description")

    for schema in agent.tool_registry.get_tools_schema():
        table.add_row(schema["name"], schema.get("description", ""))

    console.print(table)
    console.print()
