"""Workspace context — auto-injected facts about the user's current dir.

Claude Code routinely starts every conversation knowing the cwd, the
current git branch, recently-modified files, and whether this is a git
repo or a virtualenv. That context lets the model jump straight to
'fix the bug in main.py' without the user spelling out the path.

OpenBro lacked this — every turn started from zero. This module gives
the agent a single function it can call once per session to gather
the same kind of grounded environment data, then injects the result as
a system-prompt fragment.

Cheap and read-only: walks the cwd one level, calls `git` if it's a
repo, optionally reads a `.openbro/workspace.yaml` for hand-tuned hints
(active project, common files). No background scanning; everything
runs synchronously in <100ms.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkspaceContext:
    """Snapshot of the user's current working environment."""

    cwd: str
    is_git_repo: bool = False
    git_branch: str | None = None
    git_dirty: bool = False
    is_python_project: bool = False
    is_node_project: bool = False
    project_name: str | None = None
    # Up to N recently-modified files in the cwd (relative paths)
    recent_files: list[str] = field(default_factory=list)
    # Optional user-curated hints from .openbro/workspace.yaml
    hints: dict = field(default_factory=dict)

    def render_prompt_block(self) -> str:
        """Render as a prompt fragment the system prompt builder appends."""
        if not self.cwd:
            return ""
        lines = ["## WORKSPACE", f"- cwd: `{self.cwd}`"]
        if self.project_name:
            lines.append(f"- project: **{self.project_name}**")
        if self.is_git_repo:
            dirty = " (dirty)" if self.git_dirty else ""
            lines.append(f"- git: `{self.git_branch or '?'}`{dirty}")
        if self.is_python_project:
            lines.append("- python project (pyproject.toml / setup.py / requirements.txt detected)")
        if self.is_node_project:
            lines.append("- node project (package.json detected)")
        if self.recent_files:
            files_list = ", ".join(f"`{f}`" for f in self.recent_files[:6])
            lines.append(f"- recent files: {files_list}")
        for key, val in (self.hints or {}).items():
            lines.append(f"- {key}: {val}")
        return "\n".join(lines)


# Files that signal what kind of project the cwd is. First match wins for
# project_name derivation (look in pyproject.toml -> name, package.json -> name).
_PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
_NODE_MARKERS = ("package.json",)
# Directories we DON'T want to surface as 'recent files' even if they were
# touched recently. Noise that's never the user's actual file of interest.
_SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".cache",
}


def detect(cwd: str | None = None, max_recent: int = 6) -> WorkspaceContext:
    """Inspect the current directory and return a WorkspaceContext.

    Cheap: capped wall-clock, no recursion past one level, no network.
    Designed to be called once per agent session (start) and once per
    explicit `workspace refresh` REPL command.
    """
    cwd = os.path.abspath(cwd) if cwd else os.getcwd()
    ctx = WorkspaceContext(cwd=cwd)

    p = Path(cwd)
    if not p.exists():
        return ctx

    # Project detection — order matters: a pyproject.toml with a [project]
    # name wins over a generic package.json sibling.
    for marker in _PYTHON_MARKERS:
        if (p / marker).is_file():
            ctx.is_python_project = True
            break
    for marker in _NODE_MARKERS:
        if (p / marker).is_file():
            ctx.is_node_project = True
            break

    ctx.project_name = _read_project_name(p) or p.name

    # Git data — single subprocess call with a tight timeout, swallow
    # failures (not a git repo, git not installed, anything).
    git_info = _read_git_info(p)
    if git_info:
        ctx.is_git_repo = True
        ctx.git_branch = git_info.get("branch")
        ctx.git_dirty = bool(git_info.get("dirty"))

    # Recent files — one-level walk with mtime sort, capped at max_recent.
    ctx.recent_files = _recent_top_level_files(p, limit=max_recent)

    # User-curated hints (optional)
    ctx.hints = _read_user_hints(p)
    return ctx


# ─── helpers ────────────────────────────────────────────────────────────


def _read_project_name(p: Path) -> str | None:
    """Best-effort: parse pyproject.toml [project].name, then package.json
    "name". Returns None if both fail."""
    pyproj = p / "pyproject.toml"
    if pyproj.is_file():
        try:
            import tomllib  # py311+
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[import-not-found]
            except ImportError:
                tomllib = None
        if tomllib is not None:
            try:
                with pyproj.open("rb") as f:
                    data = tomllib.load(f)
                name = data.get("project", {}).get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
            except Exception:
                pass

    pkg = p / "package.json"
    if pkg.is_file():
        try:
            import json

            with pkg.open(encoding="utf-8") as f:
                data = json.load(f)
            name = data.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except Exception:
            pass
    return None


def _read_git_info(p: Path) -> dict | None:
    """Return {branch, dirty} or None if not a git repo / git missing."""
    if not (p / ".git").exists():
        return None
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if branch.returncode != 0:
            return None
        branch_name = (branch.stdout or "").strip() or "HEAD"
    except Exception:
        return None
    dirty = False
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=3,
        )
        dirty = bool((status.stdout or "").strip())
    except Exception:
        pass
    return {"branch": branch_name, "dirty": dirty}


def _recent_top_level_files(p: Path, limit: int = 6) -> list[str]:
    """List top-level files by mtime descending. Skips known noise dirs.
    Files only (no subdirs); paths are relative to cwd."""
    out: list[tuple[float, str]] = []
    try:
        for entry in p.iterdir():
            if entry.is_dir():
                continue
            if entry.name in _SKIP_DIR_NAMES:
                continue
            if entry.name.startswith("."):
                # Hide dotfiles unless they look like project configs
                if entry.name not in ("README.md", ".env.example"):
                    continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            out.append((mtime, entry.name))
    except (PermissionError, OSError):
        return []
    out.sort(key=lambda r: r[0], reverse=True)
    return [name for _, name in out[:limit]]


def _read_user_hints(p: Path) -> dict:
    """Optional curated context. `<cwd>/.openbro/workspace.yaml` if present.

    YAML keys become bullet points in the prompt block. Lets a power user
    say 'active task: refactoring billing' without re-typing it each session.
    """
    hint_file = p / ".openbro" / "workspace.yaml"
    if not hint_file.is_file():
        return {}
    try:
        import yaml

        with hint_file.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# Cache the last detection for `workspace refresh` semantics — agents
# reuse the same context across a long REPL session without re-scanning.
_CACHE: dict = {}


def detect_cached(cwd: str | None = None, ttl_seconds: float = 60.0) -> WorkspaceContext:
    """Return a cached WorkspaceContext, refreshing past TTL."""
    key = os.path.abspath(cwd) if cwd else os.getcwd()
    now = time.monotonic()
    entry = _CACHE.get(key)
    if entry is not None:
        ctx, stamped = entry
        if now - stamped < ttl_seconds:
            return ctx
    ctx = detect(key)
    _CACHE[key] = (ctx, now)
    return ctx
