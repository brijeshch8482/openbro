"""OpenBro - terminal-first personal AI agent."""

# ─── UTF-8 stdout on Windows ─────────────────────────────────────────
# Windows default console is cp1252 — chokes on common Unicode the LLM /
# tools emit (smart quotes, narrow no-break space   in formatted
# times, Hinglish punctuation, emoji). Without this, the agent's chat()
# crashes inside print() with UnicodeEncodeError and the caller sees a
# misleading 'Rate limit hit' because the str(e) contained 'character'
# which fuzzily matched the rate-limit detector. Force UTF-8 once at
# package import — covers REPL, `openbro ask`, voice mode, MCP server,
# tests. No-op on Python < 3.7 or on terminals that don't support it.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Read version from package metadata so it always matches what pip installed.
# Hardcoding here drifts: pyproject.toml said 1.0.0b1 but this said 1.0.0-beta,
# so the installer's "verify" step kept reporting a stale version even after
# a successful upgrade. Reading via importlib.metadata removes that drift.
try:
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("openbro")
    except PackageNotFoundError:
        # Running from source without `pip install -e .`
        __version__ = "0.0.0+dev"
except ImportError:
    __version__ = "0.0.0+dev"
