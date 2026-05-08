"""OpenBro CLI - Terminal entry point."""

import click

from openbro import __version__


@click.command()
@click.version_option(version=__version__, prog_name="OpenBro")
@click.option(
    "--provider",
    "-p",
    type=click.Choice(["ollama", "anthropic", "openai", "groq"]),
    help="LLM provider to use",
)
@click.option("--model", "-m", help="Model name to use")
@click.option("--offline", is_flag=True, help="Force offline mode (Ollama only)")
@click.option("--setup", is_flag=True, help="Re-run first-time setup wizard")
@click.option("--telegram", is_flag=True, help="Run as Telegram bot instead of CLI")
@click.option("--voice", is_flag=True, help="Run in voice mode (mic + TTS)")
@click.option("--gui/--cli", default=None, help="Launch browser UI (default) or terminal REPL")
def main(provider, model, offline, setup, telegram, voice, gui):
    """OpenBro - Tera Apna AI Bro

    Open-source personal AI agent. Just run 'openbro' and start chatting!
    """
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
            config["llm"]["provider"] = "ollama"
        save_config(config)

    if telegram:
        from openbro.channels.telegram_bot import run_telegram_from_config

        run_telegram_from_config()
        return

    if voice:
        from openbro.cli.voice_mode import run_voice_mode

        run_voice_mode()
        return

    # Default surface: browser GUI. Fall back to CLI if user passed --cli or
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


if __name__ == "__main__":
    main()
