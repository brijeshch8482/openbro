"""Interactive REPL for OpenBro."""

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from openbro import __version__
from openbro.cli.wizard import needs_setup, run_wizard
from openbro.core.agent import Agent
from openbro.utils.config import get_config_dir, load_config, save_config

console = Console()

COMMANDS = [
    "help",
    "exit",
    "quit",
    "config",
    "model",
    "models",
    "pull",
    "tools",
    "storage",
    "audit",
    "memory",
    "remember",
    "forget",
    "sessions",
    "clear",
    "reset",
]
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
            console.print("\n[bold green]Bro:[/bold green] ", end="")
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

    if cmd_lower == "storage":
        _show_storage()
        return True

    if cmd_lower == "storage move":
        _move_storage()
        return True

    if cmd_lower == "pull":
        _pull_model()
        return True

    if cmd_lower.startswith("pull "):
        _pull_model(cmd[5:].strip())
        return True

    if cmd_lower == "models":
        _show_models()
        return True

    if cmd_lower == "audit":
        _show_audit()
        return True

    if cmd_lower == "memory":
        _show_memory(agent)
        return True

    if cmd_lower.startswith("remember "):
        _remember(cmd[9:].strip(), agent)
        return True

    if cmd_lower.startswith("forget "):
        _forget(cmd[7:].strip(), agent)
        return True

    if cmd_lower == "sessions":
        _show_sessions(agent)
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
    table.add_row("models", "List downloaded offline models")
    table.add_row("pull", "Download a new offline model (interactive)")
    table.add_row("pull <model>", "Download specific model (e.g. pull llama3.2:3b)")
    table.add_row("storage", "Show storage usage and paths")
    table.add_row("storage move", "Move data to a different drive/folder")
    table.add_row("audit", "Show recent tool execution log")
    table.add_row("memory", "Show stored facts and memory stats")
    table.add_row("remember <key> <val>", "Save a fact (e.g. remember name Brijesh)")
    table.add_row("forget <key>", "Delete a fact")
    table.add_row("sessions", "List past conversation sessions")
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
        console.print(
            f"[green]Switched to provider: {model_name} ({agent.provider.name()})[/green]\n"
        )
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
    table.add_column("Risk", justify="center")
    table.add_column("Description")

    risk_styles = {"safe": "green", "moderate": "yellow", "dangerous": "red"}

    for schema in agent.tool_registry.get_tools_schema():
        name = schema["name"]
        risk = agent.tool_registry.get_risk(name)
        style = risk_styles.get(risk, "white")
        table.add_row(name, f"[{style}]{risk}[/{style}]", schema.get("description", ""))

    console.print(table)
    console.print(
        "\n[dim]Risk: safe = read-only, "
        "moderate = modifies files/opens apps, "
        "dangerous = system-level changes[/dim]\n"
    )


def _show_storage():
    from openbro.utils.storage import (
        format_size,
        get_available_drives,
        get_storage_paths,
        get_storage_size,
    )

    paths = get_storage_paths()
    sizes = get_storage_size()

    table = Table(title="Storage Info", border_style="cyan")
    table.add_column("Item", style="bold")
    table.add_column("Path")
    table.add_column("Size", justify="right")

    for key, path in paths.items():
        size = format_size(sizes.get(key, 0))
        table.add_row(key, str(path), size)

    console.print(table)

    # Show drive info
    drives = get_available_drives()
    if drives:
        console.print()
        dtable = Table(title="Drives", border_style="dim")
        dtable.add_column("Drive", style="bold")
        dtable.add_column("Free", justify="right")
        dtable.add_column("Total", justify="right")
        dtable.add_column("Used", justify="right")

        for d in drives:
            if d["used_percent"] < 80:
                style = "green"
            elif d["used_percent"] < 95:
                style = "yellow"
            else:
                style = "red"
            dtable.add_row(
                d["name"],
                f"{d['free_gb']} GB",
                f"{d['total_gb']} GB",
                f"[{style}]{d['used_percent']}%[/{style}]",
            )

        console.print(dtable)
    console.print()


def _move_storage():
    from rich.prompt import Prompt

    from openbro.utils.storage import get_storage_paths, migrate_storage, set_storage_path

    current = get_storage_paths()
    console.print(f"[dim]Current data location: {current['base']}[/dim]")

    new_path = Prompt.ask("Enter new path for OpenBro data")
    if not new_path:
        return

    from rich.prompt import Confirm

    if Confirm.ask(f"Move all data from {current['base']} to {new_path}?", default=False):
        try:
            migrate_storage(str(current["base"]), new_path)
            set_storage_path(new_path)
            console.print(f"[green]Data moved to: {new_path}[/green]\n")
        except Exception as e:
            console.print(f"[red]Move failed: {e}[/red]\n")
    else:
        console.print("[dim]Cancelled.[/dim]\n")


def _pull_model(model_name: str | None = None):
    from openbro.utils.ollama_setup import (
        is_ollama_installed,
        is_ollama_running,
        pull_model,
        show_model_picker,
        start_ollama_server,
    )

    if not is_ollama_installed():
        console.print("[red]Ollama not installed. Install from https://ollama.ai[/red]\n")
        return

    if not is_ollama_running():
        console.print("[dim]Starting Ollama server...[/dim]")
        if not start_ollama_server():
            console.print("[red]Could not start Ollama. Run 'ollama serve' manually.[/red]\n")
            return

    if model_name:
        pull_model(model_name)
    else:
        model = show_model_picker()
        if model:
            pull_model(model)
    console.print()


def _show_models():
    from openbro.utils.ollama_setup import (
        get_installed_models,
        is_ollama_installed,
        is_ollama_running,
    )

    if not is_ollama_installed():
        console.print("[yellow]Ollama not installed. No offline models available.[/yellow]\n")
        return

    if not is_ollama_running():
        console.print("[yellow]Ollama not running. Start with: ollama serve[/yellow]\n")
        return

    models = get_installed_models()
    if not models:
        console.print("[yellow]No models downloaded. Use 'pull' to download one.[/yellow]\n")
        return

    table = Table(title="Downloaded Models", border_style="cyan")
    table.add_column("Model", style="bold")

    for m in models:
        table.add_row(m)

    console.print(table)
    console.print(f"\n[dim]Total: {len(models)} model(s). Use 'pull' to download more.[/dim]\n")


def _show_audit():
    from openbro.utils.audit import get_recent_logs

    logs = get_recent_logs(limit=20)
    if not logs:
        console.print("[dim]No audit log entries yet.[/dim]\n")
        return

    risk_styles = {"safe": "green", "moderate": "yellow", "dangerous": "red"}

    table = Table(title="Recent Tool Executions (last 20)", border_style="cyan")
    table.add_column("Time", style="dim", width=19)
    table.add_column("Tool", style="bold")
    table.add_column("Risk", justify="center")
    table.add_column("Confirmed", justify="center")
    table.add_column("Result", overflow="fold")

    for entry in logs:
        ts = entry.get("timestamp", "")[:19].replace("T", " ")
        risk = entry.get("risk", "safe")
        style = risk_styles.get(risk, "white")
        confirmed = "yes" if entry.get("confirmed") else "auto"
        table.add_row(
            ts,
            entry.get("tool", "?"),
            f"[{style}]{risk}[/{style}]",
            confirmed,
            entry.get("result_preview", "")[:80],
        )

    console.print(table)
    console.print()


def _show_memory(agent: Agent):
    facts = agent.memory.all_facts()
    stats = agent.memory.stats()

    console.print(
        f"[cyan]Memory stats:[/cyan] "
        f"{stats['facts']} facts, "
        f"{stats['messages']} messages, "
        f"{stats['sessions']} sessions"
    )
    console.print(f"[dim]Current session: {agent.memory.session_id}[/dim]\n")

    if not facts:
        console.print("[dim]No facts stored yet. Use 'remember <key> <value>'.[/dim]\n")
        return

    table = Table(title="Stored Facts", border_style="cyan")
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_column("Category", style="dim")
    table.add_column("Updated", style="dim", overflow="fold")

    for f in facts[:30]:
        table.add_row(
            f["key"],
            f["value"][:80],
            f.get("category", "general"),
            f.get("updated_at", "")[:19].replace("T", " "),
        )

    console.print(table)
    console.print()


def _remember(args: str, agent: Agent):
    parts = args.split(maxsplit=1)
    if len(parts) != 2:
        console.print("[red]Usage: remember <key> <value>[/red]")
        console.print("[dim]Example: remember name Brijesh[/dim]\n")
        return
    key, value = parts
    agent.memory.remember(key, value)
    console.print(f"[green]Remembered:[/green] {key} = {value}\n")


def _forget(key: str, agent: Agent):
    if not key:
        console.print("[red]Usage: forget <key>[/red]\n")
        return
    if agent.memory.forget(key):
        console.print(f"[green]Forgot:[/green] {key}\n")
    else:
        console.print(f"[yellow]Nothing to forget for: {key}[/yellow]\n")


def _show_sessions(agent: Agent):
    sessions = agent.memory.list_sessions()
    if not sessions:
        console.print("[dim]No past sessions found.[/dim]\n")
        return

    table = Table(title="Recent Sessions", border_style="cyan")
    table.add_column("Session ID", style="bold")
    table.add_column("Channel")
    table.add_column("Started")
    table.add_column("Last Activity")

    for s in sessions[:20]:
        table.add_row(
            s["session_id"],
            s.get("channel", "cli"),
            s.get("started_at", "")[:19].replace("T", " "),
            s.get("last_activity", "")[:19].replace("T", " "),
        )

    console.print(table)
    console.print()
