"""OpenBro CLI - Terminal entry point.

Top-level command runs the agent (GUI by default, REPL via --cli).
Subcommands manage local LLM models (download / import / list).
"""

import click

from openbro import __version__


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="OpenBro")
@click.option(
    "--provider",
    "-p",
    type=click.Choice(["local", "anthropic", "openai", "groq", "google", "deepseek"]),
    help="LLM provider to use",
)
@click.option("--model", "-m", help="Model name to use")
@click.option("--offline", is_flag=True, help="Force offline mode (local LLM only)")
@click.option("--setup", is_flag=True, help="Re-run first-time setup wizard")
@click.option("--telegram", is_flag=True, help="Run as Telegram bot instead of CLI")
@click.option("--voice", is_flag=True, help="Run in voice mode (mic + TTS)")
@click.option("--gui/--cli", default=None, help="Launch desktop UI (default) or terminal REPL")
@click.option(
    "--mcp-server", is_flag=True, help="Run as MCP server (stdio, for Claude Desktop etc.)"
)
@click.option("--tray", is_flag=True, help="Run as system tray app with global hotkey")
@click.pass_context
def main(
    ctx,
    provider,
    model,
    offline,
    setup,
    telegram,
    voice,
    gui,
    mcp_server,
    tray,
):
    """OpenBro - Tera Apna AI Bro

    Open-source personal AI agent. Just run 'openbro' and start chatting!
    """
    if ctx.invoked_subcommand is not None:
        return

    if setup:
        from openbro.cli.wizard import run_wizard

        run_wizard()
        return

    # Apply CLI overrides to config
    if provider or model or offline:
        from openbro.utils.config import load_config, save_config

        config = load_config()
        if provider:
            config["llm"]["provider"] = provider
        if model:
            config["llm"]["model"] = model
        if offline:
            config["llm"]["provider"] = "local"
        save_config(config)

    if mcp_server:
        from openbro.mcp.server import run_mcp_server

        run_mcp_server()
        return

    if tray:
        from openbro.ui.tray import run_tray

        run_tray()
        return

    if telegram:
        from openbro.channels.telegram_bot import run_telegram_from_config

        run_telegram_from_config()
        return

    if voice:
        from openbro.cli.voice_mode import run_voice_mode

        run_voice_mode()
        return

    # Default surface: desktop GUI. Fall back to CLI if user passed --cli or
    # if the GUI deps aren't installed.
    if gui is False:
        from openbro.cli.repl import start_repl

        start_repl()
        return

    try:
        from openbro.ui.desktop import run_desktop

        run_desktop()
        return
    except ImportError:
        if gui is True:
            print("GUI deps missing. Run: pip install 'openbro[gui]'")
            return
        # gui=None (default): silently fall back to CLI
        from openbro.cli.repl import start_repl

        start_repl()


# ─── `openbro model …` subcommands ────────────────────────────────────


@main.group()
def model():
    """Manage local LLM models (download / import / list)."""
    pass


@model.command("download")
@click.argument("name")
def model_download(name: str):
    """Download a GGUF model from HuggingFace.

    NAME is one of the registry keys (e.g. llama3.1:8b, mistral:7b, phi3:mini).
    Run `openbro model list` with no GGUFs yet to see the catalogue.
    """
    from openbro.utils.local_llm_setup import (
        MODELS,
        download_model,
        ensure_llama_cpp_python,
    )

    if name not in MODELS:
        click.echo(f"Unknown model: {name}", err=True)
        click.echo(f"Available: {', '.join(MODELS.keys())}", err=True)
        raise SystemExit(2)
    if not ensure_llama_cpp_python():
        raise SystemExit(1)
    path = download_model(name)
    if not path:
        raise SystemExit(1)


@model.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
def model_import(path: str):
    """Import a GGUF file you already have (e.g. transferred via USB)."""
    from openbro.utils.local_llm_setup import import_model

    p = import_model(path)
    if not p:
        raise SystemExit(1)


@model.command("list")
def model_list():
    """Show downloaded local models and the catalogue of pullable ones."""
    from openbro.utils.local_llm_setup import MODELS, list_installed, models_dir

    md = models_dir()
    click.echo(f"Models directory: {md}")
    installed = list_installed()
    if installed:
        click.echo("\nInstalled:")
        for f in installed:
            size_gb = f.stat().st_size / 1e9
            click.echo(f"  {f.name}  ({size_gb:.1f} GB)")
    else:
        click.echo("\nNo local models installed yet.")

    click.echo("\nAvailable to download:")
    for name, info in MODELS.items():
        click.echo(f"  {name:<18} {info['size']:<8} {info['desc']}")
    click.echo("\nDownload with:  openbro model download <name>")


@model.command("remove")
@click.argument("name")
def model_remove(name: str):
    """Delete a downloaded GGUF file from the models dir."""
    from openbro.utils.local_llm_setup import MODELS, models_dir

    info = MODELS.get(name)
    md = models_dir()
    target = md / info["file"] if info else md / name
    if not target.exists():
        click.echo(f"Not found: {target}", err=True)
        raise SystemExit(1)
    if not click.confirm(f"Delete {target.name}?", default=False):
        return
    target.unlink()
    click.echo(f"Removed: {target.name}")


# ─── `openbro mcp …` subcommands ──────────────────────────────────────


@main.group()
def mcp():
    """Manage MCP servers (status, set credentials)."""
    pass


@mcp.command("status")
def mcp_status():
    """Show all configured MCP servers and whether they have what they need."""
    from openbro.utils.config import load_config

    cfg = load_config()
    servers = cfg.get("mcp", {}).get("servers", []) or []
    if not servers:
        click.echo("No MCP servers configured. Run: openbro --setup")
        return
    click.echo(f"{len(servers)} MCP server(s) configured:")
    for s in servers:
        name = s.get("name", "?")
        enabled = "on" if s.get("enabled", True) else "off"
        env = s.get("env") or {}
        missing = [k for k, v in env.items() if not v]
        status = f"  {name:<14} [{enabled}]"
        if missing:
            status += f"  needs creds: {', '.join(missing)}"
        else:
            status += "  ready"
        click.echo(status)


@mcp.command("creds")
@click.argument("server_name")
def mcp_creds(server_name: str):
    """Set credentials for an MCP server interactively.

    Example: openbro mcp creds github   →  prompts for the token, saves it
    to config.mcp.servers[<idx>].env.GITHUB_PERSONAL_ACCESS_TOKEN.
    """
    from openbro.utils.config import load_config, save_config

    cfg = load_config()
    servers = cfg.get("mcp", {}).get("servers", []) or []
    match = next(
        (i for i, s in enumerate(servers) if s.get("name") == server_name),
        None,
    )
    if match is None:
        click.echo(f"No MCP server named '{server_name}'. Try: openbro mcp status", err=True)
        raise SystemExit(1)
    server = servers[match]
    env = server.setdefault("env", {})
    if not env:
        click.echo(f"'{server_name}' doesn't need any credentials. Already ready.")
        return
    for key in list(env.keys()):
        current = env.get(key) or ""
        prompt = f"{key}"
        if current:
            prompt += " (leave empty to keep existing)"
        prompt += ": "
        value = click.prompt(prompt, hide_input=True, default="", show_default=False)
        if value.strip():
            env[key] = value.strip()
    save_config(cfg)
    click.echo(f"Updated credentials for '{server_name}'. Restart openbro to activate.")


if __name__ == "__main__":
    main()
