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

    # Step 1: Storage location FIRST so the offline-model download in step 2
    # can use the chosen drive via OLLAMA_MODELS env var, instead of dumping
    # 5+ GB into C: by default.
    _step_storage(config)

    # Step 2: LLM provider (uses storage path from step 1 for Ollama models)
    _step_provider(config)

    # Step 3: Safety settings
    _step_safety(config)

    # Step 4: Personality
    _step_personality(config)

    # Step 5: Voice mode (optional, hands-free)
    _step_voice(config)

    # Step 6: Telegram (optional)
    _step_telegram(config)

    # Step 7: MCP servers (optional, plug in external data sources)
    _step_mcp(config)

    # Save config
    save_config(config)

    console.print("\n[bold green]Setup complete![/bold green]")
    console.print(f"[dim]Config saved to: {get_config_path()}[/dim]")

    storage_base = config.get("storage", {}).get("base_dir", str(get_config_dir()))
    console.print(f"[dim]Data stored at: {storage_base}[/dim]")
    console.print("\n[bold cyan]Type anything to start chatting with your AI Bro![/bold cyan]\n")


# Comprehensive provider catalogue. Order = display order in the wizard.
# Each entry: name, tier, default model, signup URL, blurb, key prompt.
PROVIDER_CATALOG = [
    {
        "id": "groq",
        "name": "Groq",
        "tier": "FREE",
        "default_model": "llama-3.3-70b-versatile",
        "signup": "https://console.groq.com",
        "blurb": "Ultra-fast (<1s), free tier 30 req/min, hosts Llama 3.3, Mixtral, Gemma",
        "tags": ["recommended"],
    },
    {
        "id": "google",
        "name": "Google Gemini",
        "tier": "FREE",
        "default_model": "gemini-1.5-flash",
        "signup": "https://aistudio.google.com/apikey",
        "blurb": "Free tier 1500 req/day, huge context window, Gemini 1.5 Flash/Pro",
        "tags": [],
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "tier": "FREE-TRIAL",
        "default_model": "gpt-4o-mini",
        "signup": "https://platform.openai.com/api-keys",
        "blurb": "$5 trial credit; GPT-4o (paid), GPT-4o-mini (cheap)",
        "tags": [],
    },
    {
        "id": "anthropic",
        "name": "Anthropic Claude",
        "tier": "PAID",
        "default_model": "claude-sonnet-4-20250514",
        "signup": "https://console.anthropic.com/settings/keys",
        "blurb": "Best quality + tool calling. Claude Sonnet/Opus. Paid per token.",
        "tags": ["best-quality"],
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "tier": "CHEAP",
        "default_model": "deepseek-chat",
        "signup": "https://platform.deepseek.com",
        "blurb": "$0.14/M tokens; strong reasoning, OpenAI-compatible API",
        "tags": ["cheap"],
    },
    {
        "id": "ollama",
        "name": "Ollama (local)",
        "tier": "OFFLINE",
        "default_model": "llama3.1:8b",
        "signup": "https://ollama.com",
        "blurb": "Runs models on YOUR hardware. Free forever, no internet, fully private.",
        "tags": ["advanced"],
    },
]


def _step_provider(config: dict):
    # Honor user's storage choice from step 1 - Ollama writes models to the
    # path in OLLAMA_MODELS env var. Set it BEFORE pulling so model files
    # land on the chosen drive, not C: by default.
    import os

    models_dir = config.get("storage", {}).get("models_dir")
    if models_dir:
        os.environ["OLLAMA_MODELS"] = models_dir

    console.print("[bold yellow]Step 2:[/bold yellow] Choose your LLM\n")
    console.print(
        "[dim]Free, paid, and offline options. You can switch anytime via "
        "'model switch <name>'. OpenBro auto-checks for new releases daily.[/dim]\n"
    )

    table = Table(border_style="cyan")
    table.add_column("#", style="cyan", width=3)
    table.add_column("Provider", style="bold")
    table.add_column("Tier", justify="center")
    table.add_column("Default model")
    table.add_column("About", overflow="fold")

    tier_color = {
        "FREE": "green",
        "FREE-TRIAL": "green",
        "CHEAP": "yellow",
        "PAID": "red",
        "OFFLINE": "blue",
    }
    for i, p in enumerate(PROVIDER_CATALOG, 1):
        tier_str = f"[{tier_color.get(p['tier'], 'white')}]{p['tier']}[/]"
        recommended = " <-- recommended" if "recommended" in p["tags"] else ""
        table.add_row(
            str(i),
            p["name"] + recommended,
            tier_str,
            p["default_model"],
            p["blurb"],
        )

    console.print(table)
    console.print()
    choices = [str(i) for i in range(1, len(PROVIDER_CATALOG) + 1)]
    choice_idx = int(Prompt.ask("Select provider", choices=choices, default="1")) - 1
    chosen = PROVIDER_CATALOG[choice_idx]
    pid = chosen["id"]

    # Ollama gets the existing full-setup flow (install + model picker + download)
    if pid == "ollama":
        from openbro.utils.ollama_setup import full_ollama_setup

        config["llm"]["provider"] = "ollama"
        model = full_ollama_setup()
        if model:
            config["llm"]["model"] = model
            console.print(f"\n[green]Ollama ready with model: {model}[/green]\n")
        else:
            model = chosen["default_model"]
            config["llm"]["model"] = model
            console.print(f"[yellow]Ollama setup skipped. Default model: {model}[/yellow]\n")
        return

    # Cloud providers: ask for API key, configure
    config["llm"]["provider"] = pid
    console.print(f"\n[cyan]Sign up / get key:[/cyan] {chosen['signup']}\n")
    api_key = Prompt.ask(f"{chosen['name']} API key", password=True)
    if not api_key:
        console.print(
            "[yellow]Skipped. Set later with: "
            f"openbro config set providers.{pid}.api_key YOUR_KEY[/yellow]\n"
        )
        return

    config.setdefault("providers", {}).setdefault(pid, {})
    config["providers"][pid]["api_key"] = api_key
    model = Prompt.ask("Model", default=chosen["default_model"])
    config["providers"][pid]["model"] = model
    config["llm"]["model"] = model
    console.print(f"[green]{chosen['name']} selected: {model}[/green]\n")


def _step_storage(config: dict):
    console.print("[bold yellow]Step 1:[/bold yellow] Choose storage location\n")
    console.print("[dim]OpenBro stores memory, chat history, cache, and logs locally.[/dim]")
    console.print("[dim]Offline models (Ollama, ~5 GB each) will go in <path>/models.[/dim]\n")

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

    import sys

    # Voice deps (faster-whisper, sounddevice, etc.) often LACK pre-built
    # wheels for very-recent Python versions (3.14 came out Oct 2025; wheels
    # take weeks/months to catch up). Without wheels, pip falls back to
    # source builds that pull in torch/cuda/ctranslate2 — which can take
    # 30+ minutes and sometimes segfault, killing the whole shell.
    #
    # If we detect a Python version that's likely too new for voice wheels,
    # we surface that upfront and let the user opt out cleanly.
    py_major, py_minor = sys.version_info[:2]
    too_new = (py_major, py_minor) >= (3, 14)

    # Check if voice deps are already installed
    try:
        import faster_whisper  # noqa: F401
        import sounddevice  # noqa: F401

        deps_ok = True
    except Exception:
        deps_ok = False

    if deps_ok:
        # Already installed — skip the install dance
        pass
    else:
        if too_new:
            console.print(
                f"[yellow]Voice deps don't have pre-built wheels for "
                f"Python {py_major}.{py_minor} yet.[/yellow]"
            )
            console.print(
                "[dim]Source-build attempt would pull in torch/ctranslate2 "
                "(30+ min, often crashes). Recommended: skip voice for now, or "
                "downgrade to Python 3.12.[/dim]\n"
            )
            if not Confirm.ask(
                "Try voice install anyway? (will likely fail; you can skip)",
                default=False,
            ):
                config["voice"]["auto_start"] = False
                console.print(
                    "[dim]Voice skipped. Text + voice via cloud LLMs still works "
                    "fine. To add voice later: install Python 3.12 and run "
                    "'pip install openbro[voice]'.[/dim]\n"
                )
                return
        else:
            console.print(
                "[yellow]Voice dependencies missing.[/yellow] "
                "[dim]Install with: pip install 'openbro[voice]'[/dim]\n"
            )
            if not Confirm.ask("Install voice dependencies now?", default=True):
                config["voice"]["auto_start"] = False
                console.print(
                    "[dim]Skipped. Use 'openbro --voice' later if you install them.[/dim]\n"
                )
                return

        import subprocess

        console.print("[dim]Installing voice deps (1-2 min, wheel-only)...[/dim]")
        try:
            # --only-binary=:all: forces pip to refuse source builds.
            # Cleaner: either a wheel exists and installs in seconds, or pip
            # exits with a clear 'no compatible wheel' error. NEVER hangs in
            # a 30-minute compile that segfaults the whole shell.
            #
            # We DON'T use capture_output: streaming pip's progress lets the
            # user see what's happening, and avoids buffer-related freezes.
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "--no-warn-script-location",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",  # <-- key safety flag
                    "faster-whisper>=1.0",
                    "edge-tts>=6.1",
                    "sounddevice>=0.4",
                    "numpy>=1.24",
                    "pyttsx3>=2.90",
                ],
                check=False,
                timeout=300,  # hard cap: 5 min max
            )
            if result.returncode != 0:
                console.print(f"[red]Voice deps install returned exit {result.returncode}[/red]")
                console.print(
                    "[dim]Most common cause: no compatible wheel for your "
                    f"Python ({py_major}.{py_minor}). Skipping voice; rest of "
                    "OpenBro will work fine.[/dim]\n"
                )
                config["voice"]["auto_start"] = False
                return
            console.print("[green]Voice deps installed.[/green]\n")
        except subprocess.TimeoutExpired:
            console.print("[red]Voice install timed out (>5 min). Skipping.[/red]\n")
            config["voice"]["auto_start"] = False
            return
        except Exception as e:
            console.print(f"[red]Voice install error: {e}[/red]")
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


# ─── Step 7: MCP servers (curated catalogue) ─────────────────────────


MCP_CATALOG = [
    {
        "id": "filesystem",
        "name": "Filesystem",
        "needs_internet": False,
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        "extra_args_prompt": "Path to expose (e.g. C:/Users/me/Documents)",
        "blurb": "Read/write/list files in a folder you allow",
    },
    {
        "id": "github",
        "name": "GitHub",
        "needs_internet": True,
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "env_keys": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "blurb": "Repos, issues, PRs (needs personal access token)",
    },
    {
        "id": "sqlite",
        "name": "SQLite",
        "needs_internet": False,
        "command": ["uvx", "mcp-server-sqlite"],
        "extra_args_prompt": "SQLite DB path (e.g. D:/data/my.db)",
        "extra_arg_flag": "--db-path",
        "blurb": "Query a local SQLite database",
    },
    {
        "id": "time",
        "name": "Time",
        "needs_internet": False,
        "command": ["uvx", "mcp-server-time"],
        "blurb": "Time/date helpers (timezones, conversions)",
    },
    {
        "id": "fetch",
        "name": "Fetch",
        "needs_internet": True,
        "command": ["uvx", "mcp-server-fetch"],
        "blurb": "HTTP fetch + HTML-to-markdown for any URL",
    },
]


def _step_mcp(config: dict):
    console.print("\n[bold yellow]Step 7:[/bold yellow] MCP servers (optional)\n")
    console.print(
        "[dim]MCP = standardized tool plumbing. Plug filesystem, GitHub, "
        "SQLite, etc. into OpenBro as if they were built-in tools.[/dim]"
    )
    console.print("[dim]Most servers run via 'npx' (Node.js auto-installed).[/dim]\n")

    if not Confirm.ask("Configure MCP servers now?", default=False):
        console.print("[dim]Skipped. Add servers later via 'config set mcp.servers'.[/dim]\n")
        return

    table = Table(border_style="cyan")
    table.add_column("#", style="cyan", width=3)
    table.add_column("Server", style="bold")
    table.add_column("Net?", justify="center")
    table.add_column("About")
    for i, s in enumerate(MCP_CATALOG, 1):
        net = "[red]online[/]" if s["needs_internet"] else "[green]offline[/]"
        table.add_row(str(i), s["name"], net, s["blurb"])
    console.print(table)
    console.print()

    raw = Prompt.ask(
        "Pick servers (comma-separated numbers, e.g. 1,3) or 'none'",
        default="none",
    )
    if raw.strip().lower() in {"none", "", "0"}:
        console.print("[dim]No MCP servers enabled.[/dim]\n")
        return

    picks = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit():
            idx = int(tok) - 1
            if 0 <= idx < len(MCP_CATALOG):
                picks.append(MCP_CATALOG[idx])

    servers = config.setdefault("mcp", {}).setdefault("servers", [])

    for pick in picks:
        entry = {
            "name": pick["id"],
            "command": list(pick["command"]),
            "enabled": True,
        }
        # Extra runtime args (e.g. filesystem path)
        if pick.get("extra_args_prompt"):
            value = Prompt.ask(f"  {pick['name']}: {pick['extra_args_prompt']}")
            if value:
                if pick.get("extra_arg_flag"):
                    entry["command"].extend([pick["extra_arg_flag"], value])
                else:
                    entry["command"].append(value)
        # Env vars (API keys, tokens)
        if pick.get("env_keys"):
            env_dict = {}
            for key in pick["env_keys"]:
                v = Prompt.ask(f"  {pick['name']}: {key}", password=True, default="")
                if v:
                    env_dict[key] = v
            if env_dict:
                entry["env"] = env_dict
        servers.append(entry)
        console.print(f"  [green]✓ Added MCP server: {pick['name']}[/green]")

    console.print(
        f"\n[green]MCP configured with {len(picks)} server(s). "
        f"They auto-connect at OpenBro startup.[/green]\n"
    )


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
