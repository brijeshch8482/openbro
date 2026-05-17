"""OpenBro - terminal-first personal AI agent."""

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
