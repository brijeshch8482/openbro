"""OpenBro CLI - Terminal entry point."""

import click

from openbro import __version__


@click.command()
@click.version_option(version=__version__, prog_name="OpenBro")
@click.option("--provider", "-p", type=click.Choice(["ollama", "anthropic", "openai"]), help="LLM provider to use")
@click.option("--model", "-m", help="Model name to use")
@click.option("--offline", is_flag=True, help="Force offline mode (Ollama only)")
@click.option("--setup", is_flag=True, help="Re-run first-time setup wizard")
def main(provider, model, offline, setup):
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

    from openbro.cli.repl import start_repl
    start_repl()


if __name__ == "__main__":
    main()
