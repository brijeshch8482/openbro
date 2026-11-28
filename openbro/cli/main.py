"""OpenBro CLI - Terminal entry point.

Top-level command runs the terminal agent.
Subcommands manage local LLM models (download / import / list).
"""

import click

from openbro import __version__


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="OpenBro")
@click.option(
    "--provider",
    "-p",
    type=click.Choice(
        [
            "local",
            "specialist",
            "codex",
            "claude",
            "anthropic",
            "openai",
            "groq",
            "google",
            "deepseek",
        ]
    ),
    help="LLM provider to use",
)
@click.option("--model", "-m", help="Model name to use")
@click.option("--offline", is_flag=True, help="Force offline mode (local LLM only)")
@click.option("--setup", is_flag=True, help="Re-run first-time setup wizard")
@click.option("--telegram", is_flag=True, help="Run as Telegram bot instead of CLI")
@click.option("--voice", is_flag=True, help="Run in voice mode (mic + TTS)")
@click.option(
    "--mcp-server", is_flag=True, help="Run as MCP server (stdio, for Claude Desktop etc.)"
)
@click.option(
    "--resume",
    "-r",
    "resume_session",
    is_flag=False,
    flag_value="latest",
    default=None,
    help=(
        "Resume a previous session. With no value, resumes the most recent. "
        "Pass a session ID (e.g. --resume abc123def456) to load a specific one. "
        "Use `sessions` REPL command to see options."
    ),
)
@click.pass_context
def main(
    ctx,
    provider,
    model,
    offline,
    setup,
    telegram,
    voice,
    mcp_server,
    resume_session,
):
    """OpenBro - terminal-first personal AI agent.

    Just run 'openbro' and start chatting.
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

    if telegram:
        from openbro.channels.telegram_bot import run_telegram_from_config

        run_telegram_from_config()
        return

    if voice:
        from openbro.cli.voice_mode import run_voice_mode

        run_voice_mode()
        return

    from openbro.cli.repl import start_repl

    start_repl(resume_session=resume_session)


# ─── `openbro ask "<query>"` — single-shot non-interactive ────────────
# Lets you script the agent (CI, automation, verification) without
# entering the REPL. The agent loop runs the same way; output goes
# to stdout so the caller can capture it. Exit code 0 on success,
# 1 on agent error.


@main.command("ask")
@click.argument("query", nargs=-1, required=True)
def ask(query):
    """Ask one question, get one answer, exit. Non-interactive.

    Example: openbro ask "how many Word files are on my desktop?"
    """
    user_input = " ".join(query)
    from openbro.core.agent import Agent
    from openbro.core.permissions import PermissionGate

    # auto-permission mode for scripted use — no interactive prompts.
    # If the user wants strict gating they can use the REPL or set
    # safety.permission_mode in config to 'boss'.
    gate = PermissionGate(mode="auto", channel="silent")
    agent = Agent(interactive=False, permission_gate=gate)
    try:
        response = agent.chat(user_input)
        click.echo(response)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from e


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


@model.command("update", hidden=True)
@click.option(
    "--repo",
    default="brijeshch8482/openbro-1b-instruct",
    show_default=True,
    help="HuggingFace repo id hosting openbro.gguf.",
)
@click.option(
    "--file",
    default="openbro.gguf",
    show_default=True,
    help="Filename inside the HF repo.",
)
@click.option("--force", is_flag=True, help="Re-download even if local copy already current.")
def model_update(repo: str, file: str, force: bool):
    """Pull the latest openbro.gguf from HuggingFace (experimental, hidden).

    OpenBro does not ship its own model; this is kept for the experimental
    training pipeline. End users run this when they want to refresh OpenBro's custom
    model. The maintainer's weekly training cycle publishes a new
    openbro.gguf to the HuggingFace repo; this command downloads it
    and replaces the local copy (backing up the previous version).
    """
    import shutil
    import time as _time

    from openbro.utils.local_llm_setup import models_dir

    md = models_dir()
    md.mkdir(parents=True, exist_ok=True)
    local_path = md / file

    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        click.echo(
            "huggingface_hub is required. Install it:\n  pip install 'openbro[local]'",
            err=True,
        )
        raise SystemExit(1) from None

    # Resolve the remote file's sha256 (lets us skip if unchanged).
    api = HfApi()
    try:
        remote_info = api.model_info(repo, files_metadata=True)
    except Exception as e:
        click.echo(f"Couldn't reach HuggingFace ({repo}): {e}", err=True)
        raise SystemExit(1) from e

    remote_sha = None
    for sib in remote_info.siblings or []:
        if sib.rfilename == file:
            remote_sha = getattr(sib, "lfs", None) and sib.lfs.get("sha256")
            break
    if remote_sha is None:
        click.echo(f"Remote file '{file}' not found in {repo}", err=True)
        raise SystemExit(1)

    if local_path.exists() and not force:
        # Quick sha check — compare just the head of the file as a
        # cheap signal; sha256 of multi-GB files is slow.
        import hashlib

        h = hashlib.sha256()
        with local_path.open("rb") as f:
            while chunk := f.read(8 * 1024 * 1024):
                h.update(chunk)
        if h.hexdigest() == remote_sha:
            click.echo(f"Already up to date: {file}")
            return

    backup_dir = md / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if local_path.exists():
        stamp = _time.strftime("%Y%m%d-%H%M%S")
        bak = backup_dir / f"{file}.{stamp}.bak"
        shutil.move(str(local_path), str(bak))
        click.echo(f"Backup: {bak.name}")

    click.echo(f"Downloading {file} from {repo} …")
    try:
        downloaded = hf_hub_download(
            repo_id=repo,
            filename=file,
            cache_dir=str(md / ".hf_cache"),
            local_dir=str(md),
            local_dir_use_symlinks=False,
        )
    except Exception as e:
        click.echo(f"Download failed: {e}", err=True)
        raise SystemExit(1) from e

    # hf_hub_download writes into models_dir; verify the target exists.
    if not local_path.exists():
        # If the downloader put it elsewhere, copy in.
        shutil.copy2(downloaded, str(local_path))
    size_mb = round(local_path.stat().st_size / (1024 * 1024), 1)
    click.echo(f"Updated: {file} ({size_mb} MB)")
    click.echo("Next REPL start will use the new model.")


@model.command("info")
def model_info():
    """Show current local model and its source."""
    from openbro.utils.config import load_config
    from openbro.utils.local_llm_setup import models_dir

    cfg = load_config()
    local_cfg = cfg.get("providers", {}).get("local", {})
    name = local_cfg.get("model", "?")
    md = models_dir()
    click.echo(f"Configured local model: {name}")
    click.echo(f"Models directory: {md}")
    if not md.exists():
        click.echo("  (directory does not exist yet)")
        return
    for f in sorted(md.glob("*.gguf")):
        size_mb = round(f.stat().st_size / (1024 * 1024), 1)
        click.echo(f"  {f.name}  ({size_mb} MB)")


# ─── `openbro config …` subcommands ───────────────────────────────────


@main.group("config")
def config_group():
    """View or update OpenBro configuration."""
    pass


@config_group.command("show")
def config_show():
    """Print the full config.yaml."""
    import yaml

    from openbro.utils.config import get_config_path, load_config

    cfg = load_config()
    click.echo(f"# {get_config_path()}")
    click.echo(yaml.dump(cfg, default_flow_style=False, sort_keys=False))


@config_group.command("get")
@click.argument("key_path")
def config_get(key_path: str):
    """Read one value by dotted path (e.g. llm.model)."""
    from openbro.utils.config import load_config

    cfg = load_config()
    obj = cfg
    for k in key_path.split("."):
        if not isinstance(obj, dict) or k not in obj:
            click.echo(f"Not set: {key_path}", err=True)
            raise SystemExit(1)
        obj = obj[k]
    if isinstance(obj, (dict, list)):
        import yaml

        click.echo(yaml.dump(obj, default_flow_style=False, sort_keys=False).rstrip())
    else:
        click.echo(obj)


@config_group.command("set")
@click.argument("key_path")
@click.argument("value")
def config_set(key_path: str, value: str):
    """Update one value by dotted path (e.g. llm.model llama3.2:3b)."""
    from openbro.utils.config import load_config, save_config

    cfg = load_config()
    keys = key_path.split(".")
    obj = cfg
    for k in keys[:-1]:
        if not isinstance(obj.get(k), dict):
            obj[k] = {}
        obj = obj[k]

    # Type coerce common cases
    coerced: object = value
    low = value.strip().lower()
    if low in ("true", "yes", "on"):
        coerced = True
    elif low in ("false", "no", "off"):
        coerced = False
    elif low in ("none", "null", "~"):
        coerced = None
    elif value.lstrip("-").isdigit():
        coerced = int(value)
    else:
        try:
            coerced = float(value)
        except ValueError:
            pass  # keep as string

    obj[keys[-1]] = coerced
    save_config(cfg)
    click.echo(f"Set {key_path} = {coerced}")


# ─── `openbro train` — experimental training pipeline (hidden) ───────


def _register_train_command():
    """Register the train command lazily so the heavy ML imports in
    openbro.training don't slow down `openbro` startup for users who
    aren't training."""
    try:
        from openbro.training.cli import train as train_cmd

        # Experimental maintainer pipeline; OpenBro ships no model of its
        # own, so the command is kept but hidden from --help.
        train_cmd.hidden = True
        main.add_command(train_cmd)
    except Exception:
        # Training extras (torch, peft, etc.) optional — if missing,
        # the command simply isn't available.
        pass


_register_train_command()


# ─── `openbro doctor / update / uninstall` ───────────────────────────


@main.command("doctor")
def doctor():
    """Check the install: Python, config, provider key, storage, optional features."""
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    from openbro.cli.maintenance import doctor_checks

    marks = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✗[/red]"}
    table = Table(title="OpenBro doctor", show_lines=False, border_style="cyan")
    table.add_column("", width=2)
    table.add_column("Check", style="bold")
    table.add_column("Details", overflow="fold")
    rows = doctor_checks()
    for name, status, detail in rows:
        table.add_row(marks.get(status, status), escape(name), escape(detail))
    Console().print(table)
    failed = [r for r in rows if r[1] == "fail"]
    if failed:
        raise SystemExit(1)


@main.command("web")
@click.option("--host", default="127.0.0.1", show_default=True, help="Address to listen on.")
@click.option("--port", default=8000, show_default=True, type=int, help="Port to listen on.")
@click.option(
    "--docs",
    "docs",
    multiple=True,
    type=click.Path(exists=True),
    help="File or folder visitors can search (repeatable). Default: README + docs/.",
)
@click.option("--no-docs", is_flag=True, help="Run without document search (no `docs` tool).")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Where the demo's document index lives. Default: ~/.openbro/web.",
)
@click.option("--per-ip", default=20, show_default=True, type=int, help="Messages per IP per hour.")
@click.option(
    "--trust-proxy",
    is_flag=True,
    help="Take the client IP from X-Forwarded-For (behind a hosting proxy).",
)
def web(host, port, docs, no_docs, data_dir, per_ip, trust_proxy):
    """Serve the public demo: a chat page where anyone can talk to OpenBro.

    Visitors get a locked-down agent — web search, date/time and search over
    the demo documents only; no shell, files, memory or host context.
    Set GROQ_API_KEY to answer with a free Groq model.
    """
    from pathlib import Path

    try:
        import uvicorn
    except ImportError:
        click.echo("The web demo needs FastAPI + uvicorn:  pip install 'openbro[web]'", err=True)
        raise SystemExit(1)

    from openbro.web.server import build_backend, create_app
    from openbro.web.sessions import WebSettings

    settings = WebSettings(per_ip_per_hour=per_ip, trust_proxy=trust_proxy)
    sources = [] if no_docs else ([Path(d) for d in docs] if docs else None)
    base = Path(data_dir) if data_dir else Path.home() / ".openbro" / "web"
    click.echo("Preparing the demo (indexing documents on first run)…")
    backend = build_backend(settings, sources, base)
    click.echo(f"Model: {backend.model_name}  ·  tools: {', '.join(backend.tool_names)}")
    click.echo(f"OpenBro web demo on http://{host}:{port}")
    uvicorn.run(create_app(backend), host=host, port=port, log_level="warning")


@main.command("update")
@click.option(
    "--source",
    type=click.Choice(["git", "pypi"]),
    default="git",
    show_default=True,
    help="Install the latest main branch from GitHub, or the latest PyPI release.",
)
@click.option(
    "--extras",
    default=None,
    help="Extras to install, e.g. 'all,voice,rag'. Default: keep what you have.",
)
@click.option("--dry-run", is_flag=True, help="Only print the commands.")
def update(source: str, extras: str | None, dry_run: bool):
    """Update OpenBro in place, keeping your config, memory and models."""
    from openbro.cli.maintenance import run_update

    chosen = [e.strip() for e in extras.split(",") if e.strip()] if extras is not None else None
    raise SystemExit(run_update(source, chosen, dry_run, click.echo))


@main.command("uninstall")
@click.option("--yes", "-y", is_flag=True, help="Don't ask for confirmation.")
@click.option("--keep-data", is_flag=True, help="Keep config, memory, document index and models.")
@click.option("--keep-models", is_flag=True, help="Keep downloaded local models.")
def uninstall(yes: bool, keep_data: bool, keep_models: bool):
    """Remove OpenBro and (unless kept) its data folders."""
    from openbro.cli.maintenance import run_uninstall

    raise SystemExit(run_uninstall(yes, keep_data, keep_models, click.confirm, click.echo))


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
