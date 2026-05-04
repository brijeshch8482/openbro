"""First-run wizard - interactive setup on first launch."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from openbro.utils.config import default_config, get_config_dir, get_config_path, save_config
from openbro.utils.storage import (
    detect_cloud_folders,
    get_available_drives,
    set_storage_path,
)

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
    console.print(
        Panel(
            BANNER,
            title="[bold cyan]Welcome to OpenBro![/bold cyan]",
            border_style="cyan",
        )
    )
    console.print("[bold]Tera Apna AI Bro - Open-Source Personal AI Agent[/bold]")
    console.print("[dim]Let's set you up in under 2 minutes.\n[/dim]")

    config = default_config()

    # Step 1: Choose LLM provider
    _step_provider(config)

    # Step 2: Storage location
    _step_storage(config)

    # Step 3: Safety settings
    _step_safety(config)

    # Step 4: Personality
    _step_personality(config)

    # Step 5: Voice mode (optional, hands-free)
    _step_voice(config)

    # Step 6: Telegram (optional)
    _step_telegram(config)

    # Save config
    save_config(config)

    console.print("\n[bold green]Setup complete![/bold green]")
    console.print(f"[dim]Config saved to: {get_config_path()}[/dim]")

    storage_base = config.get("storage", {}).get("base_dir", str(get_config_dir()))
    console.print(f"[dim]Data stored at: {storage_base}[/dim]")
    console.print("\n[bold cyan]Type anything to start chatting with your AI Bro![/bold cyan]\n")


def _step_provider(config: dict):
    console.print("[bold yellow]Step 1:[/bold yellow] Choose your LLM provider\n")
    console.print("  [cyan]1.[/cyan] Ollama (offline, free, local) [green]<-- recommended[/green]")
    console.print("  [cyan]2.[/cyan] Groq (cloud, free tier, ultra-fast)")
    console.print("  [cyan]3.[/cyan] Anthropic (Claude API, paid)")
    console.print("  [cyan]4.[/cyan] OpenAI (GPT API, paid)")
    console.print()

    choice = Prompt.ask("Select provider", choices=["1", "2", "3", "4"], default="1")

    if choice == "1":
        config["llm"]["provider"] = "ollama"

        # Auto-setup: install Ollama, pick model, download it
        from openbro.utils.ollama_setup import full_ollama_setup

        model = full_ollama_setup()
        if model:
            config["llm"]["model"] = model
            console.print(f"\n[green]Ollama ready with model: {model}[/green]\n")
        else:
            # Fallback - user skipped, set default
            model = "qwen2.5-coder:7b"
            config["llm"]["model"] = model
            console.print(f"[yellow]Ollama setup skipped. Default model set: {model}[/yellow]")
            console.print("[dim]Download later: ollama pull qwen2.5-coder:7b[/dim]\n")

    elif choice == "2":
        config["llm"]["provider"] = "groq"
        api_key = Prompt.ask("Groq API key (free at console.groq.com)")
        config["providers"]["groq"]["api_key"] = api_key
        model = Prompt.ask("Model", default="llama-3.3-70b-versatile")
        config["providers"]["groq"]["model"] = model
        config["llm"]["model"] = model
        console.print(f"[green]Groq selected: {model}[/green]\n")

    elif choice == "3":
        config["llm"]["provider"] = "anthropic"
        api_key = Prompt.ask("Anthropic API key")
        config["providers"]["anthropic"]["api_key"] = api_key
        model = Prompt.ask("Model", default="claude-sonnet-4-20250514")
        config["providers"]["anthropic"]["model"] = model
        config["llm"]["model"] = model
        console.print(f"[green]Anthropic selected: {model}[/green]\n")

    elif choice == "4":
        config["llm"]["provider"] = "openai"
        api_key = Prompt.ask("OpenAI API key")
        config["providers"]["openai"]["api_key"] = api_key
        model = Prompt.ask("Model", default="gpt-4o")
        config["providers"]["openai"]["model"] = model
        config["llm"]["model"] = model
        console.print(f"[green]OpenAI selected: {model}[/green]\n")


def _step_storage(config: dict):
    console.print("[bold yellow]Step 2:[/bold yellow] Choose storage location\n")
    console.print("[dim]OpenBro stores memory, chat history, cache, and logs locally.[/dim]")
    console.print("[dim]Offline models (Ollama) are stored separately.[/dim]\n")

    # Show available drives
    drives = get_available_drives()
    if drives:
        table = Table(title="Available Drives", border_style="dim")
        table.add_column("#", style="cyan", width=3)
        table.add_column("Drive", style="bold")
        table.add_column("Free Space", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Used %", justify="right")

        for i, drive in enumerate(drives, 1):
            if drive["used_percent"] < 80:
                used_style = "green"
            elif drive["used_percent"] < 95:
                used_style = "yellow"
            else:
                used_style = "red"
            table.add_row(
                str(i),
                drive["name"],
                f"{drive['free_gb']} GB",
                f"{drive['total_gb']} GB",
                f"[{used_style}]{drive['used_percent']}%[/{used_style}]",
            )
        console.print(table)

    # Check for cloud folders
    cloud_folders = detect_cloud_folders()
    if cloud_folders:
        console.print("\n[dim]Cloud sync folders detected:[/dim]")
        for cf in cloud_folders:
            console.print(f"  [cyan]*[/cyan] {cf['name']}: {cf['path']} ({cf['free_gb']} GB free)")
        console.print(
            "[dim yellow]Note: Cloud folders have sync risks"
            " - use only for backup, not primary storage."
            "[/dim yellow]"
        )

    console.print()

    # Storage options
    default_dir = str(get_config_dir())
    console.print(f"  [cyan]1.[/cyan] Default ({default_dir}) [green]<-- recommended[/green]")
    console.print("  [cyan]2.[/cyan] Custom path (choose your own drive/folder)")
    if cloud_folders:
        console.print("  [cyan]3.[/cyan] Cloud folder (Google Drive / OneDrive / Dropbox)")
    console.print()

    max_choice = "3" if cloud_folders else "2"
    storage_choice = Prompt.ask(
        "Select storage location",
        choices=[str(i) for i in range(1, int(max_choice) + 1)],
        default="1",
    )

    if storage_choice == "1":
        config["storage"] = {"base_dir": default_dir, "models_dir": default_dir + "/models"}
        console.print(f"[green]Data will be stored at: {default_dir}[/green]\n")

    elif storage_choice == "2":
        custom_path = Prompt.ask("Enter full path for OpenBro data", default="D:\\OpenBro-Data")
        custom_path = str(Path(custom_path).resolve())
        config["storage"] = {"base_dir": custom_path, "models_dir": custom_path + "/models"}

        # Ask if models should go to different location
        if Confirm.ask("Store offline models at a different location?", default=False):
            models_path = Prompt.ask("Enter path for models", default=custom_path + "/models")
            config["storage"]["models_dir"] = str(Path(models_path).resolve())

        set_storage_path(
            config["storage"]["base_dir"],
            config["storage"]["models_dir"],
        )
        console.print(f"[green]Data: {config['storage']['base_dir']}[/green]")
        console.print(f"[green]Models: {config['storage']['models_dir']}[/green]\n")

    elif storage_choice == "3" and cloud_folders:
        console.print("\n[yellow]Cloud storage warning:[/yellow]")
        console.print("  - Data will sync to cloud (privacy consideration)")
        console.print("  - Sync conflicts possible if used on multiple machines")
        console.print("  - Offline mode won't work if cloud is syncing")
        console.print("  - Best for: backup only, not primary storage\n")

        if not Confirm.ask("Continue with cloud storage?", default=False):
            # Fall back to default
            config["storage"] = {"base_dir": default_dir, "models_dir": default_dir + "/models"}
            console.print(f"[green]Using default: {default_dir}[/green]\n")
        else:
            for i, cf in enumerate(cloud_folders, 1):
                console.print(f"  [cyan]{i}.[/cyan] {cf['name']}: {cf['path']}")

            cf_choice = Prompt.ask(
                "Select cloud folder",
                choices=[str(i) for i in range(1, len(cloud_folders) + 1)],
                default="1",
            )
            selected = cloud_folders[int(cf_choice) - 1]
            cloud_base = str(Path(selected["path"]) / "OpenBro")
            config["storage"] = {
                "base_dir": cloud_base,
                "models_dir": default_dir + "/models",  # Models stay local (too large for cloud)
                "cloud_sync": True,
                "cloud_provider": selected["name"],
            }
            set_storage_path(cloud_base, default_dir + "/models")
            console.print(f"[green]Data: {cloud_base} (synced to {selected['name']})[/green]")
            console.print(
                f"[green]Models: {default_dir}/models (local only - too large for cloud)[/green]\n"
            )


def _step_safety(config: dict):
    console.print("[bold yellow]Step 3:[/bold yellow] Safety settings\n")
    confirm_dangerous = Confirm.ask(
        "Confirm before running dangerous commands?",
        default=True,
    )
    config["safety"]["confirm_dangerous"] = confirm_dangerous


def _step_personality(config: dict):
    console.print("\n[bold yellow]Step 4:[/bold yellow] Personality\n")
    console.print("  [cyan]1.[/cyan] Hinglish Bro (default - Hindi+English mix)")
    console.print("  [cyan]2.[/cyan] English Professional")
    console.print("  [cyan]3.[/cyan] Hindi")
    console.print()

    personality = Prompt.ask("Select personality", choices=["1", "2", "3"], default="1")

    prompts = {
        "1": (
            "Tu OpenBro hai - ek helpful AI bro. Friendly aur"
            " casual reh, Hindi-English mix me baat kar. User"
            " ki help kar. Short aur to-the-point answers de."
        ),
        "2": (
            "You are OpenBro, a helpful AI assistant. Be"
            " professional, clear, and concise. Help the"
            " user with their tasks efficiently."
        ),
        "3": (
            "Tu OpenBro hai - ek helpful AI assistant."
            " Hindi me baat kar. User ki help kar."
            " Short aur clear answers de."
        ),
    }
    config["agent"]["system_prompt"] = prompts[personality]


def _step_voice(config: dict):
    console.print("\n[bold yellow]Step 5:[/bold yellow] Voice mode (hands-free)\n")
    console.print("[dim]Always-on mic + wake word ('Hey bro') + TTS reply.[/dim]")
    console.print("[dim]You can type AND speak — both work simultaneously.[/dim]\n")

    # Check voice deps
    try:
        import faster_whisper  # noqa: F401
        import sounddevice  # noqa: F401

        deps_ok = True
    except Exception:
        deps_ok = False

    if not deps_ok:
        console.print(
            "[yellow]Voice dependencies missing.[/yellow] "
            "[dim]Install with: pip install 'openbro[voice]'[/dim]\n"
        )
        if not Confirm.ask("Install voice dependencies now?", default=True):
            config["voice"]["auto_start"] = False
            console.print("[dim]Skipped. Use 'openbro --voice' later if you install them.[/dim]\n")
            return

        import subprocess
        import sys

        console.print("[dim]Installing voice deps (1-2 min)...[/dim]")
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "faster-whisper>=1.0",
                    "edge-tts>=6.1",
                    "sounddevice>=0.4",
                    "numpy>=1.24",
                    "pyttsx3>=2.90",
                ],
                check=True,
            )
            console.print("[green]Voice deps installed.[/green]\n")
        except Exception as e:
            console.print(f"[red]Install failed: {e}[/red]")
            config["voice"]["auto_start"] = False
            return

    enable_q = "Enable voice mode by default? (mic always-on, wake word activation)"
    if not Confirm.ask(enable_q, default=False):
        config["voice"]["auto_start"] = False
        console.print(
            "[dim]Skipped. Voice still works via 'openbro --voice' or 'voice on' in REPL.[/dim]\n"
        )
        return

    config["voice"]["auto_start"] = True

    # Optional: pre-download Whisper model so first wake-word doesn't lag
    if Confirm.ask(
        "Pre-download Whisper STT model now? (~140 MB, avoids lag on first use)",
        default=True,
    ):
        try:
            console.print("[dim]Downloading Whisper 'base' model...[/dim]")
            from faster_whisper import WhisperModel

            WhisperModel("base", device="cpu", compute_type="int8")
            console.print("[green]Model ready.[/green]\n")
        except Exception as e:
            console.print(f"[yellow]Pre-download failed: {e}[/yellow]")
            console.print("[dim]Will download on first wake-word instead.[/dim]\n")

    console.print("[green]Voice mode enabled.[/green] [dim]Say 'Hey bro' anytime in chat.[/dim]\n")


def _step_telegram(config: dict):
    console.print("\n[bold yellow]Step 6:[/bold yellow] Telegram bot (optional)\n")
    console.print("[dim]Control OpenBro from your phone via Telegram.[/dim]")
    console.print("[dim]You'll need a bot token from @BotFather on Telegram.[/dim]\n")

    if not Confirm.ask("Set up Telegram bot now?", default=False):
        console.print("[dim]Skipped. You can set it up later via 'config set'.[/dim]\n")
        return

    console.print("\n[cyan]How to get a Telegram bot token:[/cyan]")
    console.print("  1. Open Telegram, message @BotFather")
    console.print("  2. Send /newbot and follow prompts")
    console.print("  3. Copy the token (looks like: 1234567890:ABC...)")
    console.print()

    token = Prompt.ask("Telegram bot token (leave empty to skip)", default="")
    if not token:
        console.print("[dim]Skipped Telegram setup.[/dim]\n")
        return

    config["channels"]["telegram"]["enabled"] = True
    config["channels"]["telegram"]["token"] = token

    console.print("\n[cyan]Authorized users:[/cyan]")
    console.print(
        "[dim]Only specific Telegram user IDs can use the bot. "
        "Leave empty to allow anyone (NOT recommended).[/dim]\n"
    )
    console.print("[dim]To find your Telegram user ID, message @userinfobot on Telegram.[/dim]\n")

    ids_input = Prompt.ask(
        "Enter authorized user IDs (comma-separated, e.g. 12345,67890)",
        default="",
    )
    allowed = []
    for part in ids_input.split(","):
        part = part.strip()
        if part.isdigit():
            allowed.append(int(part))

    config["channels"]["telegram"]["allowed_users"] = allowed

    if allowed:
        console.print(f"[green]Authorized users: {allowed}[/green]")
    else:
        console.print(
            "[yellow]Warning: bot is open to anyone. "
            "Add IDs later via: config set channels.telegram.allowed_users[/yellow]"
        )

    console.print("\n[dim]Run the Telegram bot with: openbro --telegram[/dim]\n")
