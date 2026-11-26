"""User-written instructions (harness piece 3, memory L0).

`OPENBRO.md` files hold standing instructions the user writes by hand
("always reply in Hinglish", "my projects live on D:/code"). They are
loaded whole, in order:

1. `<config_dir>/OPENBRO.md` — global
2. every `OPENBRO.md` from the project root down to the cwd

Capped at `instructions_budget_tokens` in total; a file that doesn't fit
is cut with a note. Results are cached on the files' mtimes.
"""

from __future__ import annotations

from pathlib import Path

FILE_NAME = "OPENBRO.md"


def find_project_root(cwd: Path) -> Path | None:
    """Nearest ancestor (or cwd itself) that holds a `.git` entry."""
    cwd = cwd.resolve()
    for d in (cwd, *cwd.parents):
        if (d / ".git").exists():
            return d
    return None


def instruction_files(cwd: Path, config_dir: Path) -> list[Path]:
    files: list[Path] = []
    global_file = config_dir / FILE_NAME
    if global_file.is_file():
        files.append(global_file)
    cwd = cwd.resolve()
    root = find_project_root(cwd) or cwd
    chain = (
        [root]
        + [p for p in reversed(cwd.parents) if root in p.parents]
        + ([cwd] if cwd != root else [])
    )
    seen = {f.resolve() for f in files}
    for d in chain:
        f = d / FILE_NAME
        if f.is_file() and f.resolve() not in seen:
            files.append(f)
            seen.add(f.resolve())
    return files


def signature(files: list[Path]) -> tuple:
    out = []
    for f in files:
        try:
            out.append((str(f), f.stat().st_mtime_ns))
        except OSError:
            continue
    return tuple(out)


def load_instructions(files: list[Path], budget_tokens: int = 4000) -> str:
    if not files:
        return ""
    budget = budget_tokens * 4
    parts = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        block = f"### From {f}\n{text}"
        if len(block) > budget:
            block = block[: max(budget, 0)] + f"\n[…truncated — {f.name} is over the budget]"
        parts.append(block)
        budget -= len(block)
        if budget <= 0:
            break
    if not parts:
        return ""
    return "## USER INSTRUCTIONS (OPENBRO.md — written by the user, always follow)\n" + "\n\n".join(
        parts
    )
