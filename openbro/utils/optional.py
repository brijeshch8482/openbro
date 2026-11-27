"""Checks for optional packages that never import them.

Importing a half-installed package has side effects: `sentence_transformers`
pulls in `transformers`, which prints "PyTorch was not found…" to the
terminal when torch is missing. These checks only look the module up.
"""

from __future__ import annotations

import importlib.util


def has_module(name: str) -> bool:
    """True when `name` is importable, without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def sentence_transformers_usable() -> bool:
    """sentence-transformers only works with torch installed beside it."""
    return has_module("torch") and has_module("sentence_transformers")
