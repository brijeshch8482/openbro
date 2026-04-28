"""OpenBro CLI - Terminal entry point."""

import click

from openbro import __version__


@click.command()
@click.version_option(version=__version__, prog_name="OpenBro")
def main():
    """OpenBro - Tera Apna AI Bro"""
    from openbro.cli.repl import start_repl

    start_repl()


if __name__ == "__main__":
    main()
