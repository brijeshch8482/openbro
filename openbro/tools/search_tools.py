"""Search and edit tools (harness piece 4): grep, glob, read, edit.

Each does one thing and returns raw data; the model's chain of calls is
the reasoning ("grep for the function → read those lines → edit").

- `grep`  — regex search over file contents (ripgrep when installed,
            otherwise a Python walk that skips VCS/build folders and
            binary files)
- `glob`  — find files by pattern, newest first
- `read`  — numbered lines with offset/limit, so big files are read in parts
- `edit`  — exact string replacement; the file must have been `read` in
            this session and be unchanged since, and `old_string` must be
            unique unless `replace_all` is set
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from openbro.tools.base import BaseTool, RiskLevel
from openbro.utils.paths import resolve_user_path

MAX_OUTPUT_CHARS = 30_000
MAX_FILE_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".idea",
    ".vscode",
}


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[output truncated — {len(text) - limit:,} more chars]"


def _is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(4096)
    except OSError:
        return True


def _walk_files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            yield Path(dirpath) / name


# ─── read-state tracking (shared by read and edit) ──────────────────


class FileState:
    """Remembers which files were read and their mtime at that moment."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(str(path.resolve()))

    def mark(self, path: Path) -> None:
        with self._lock:
            self._seen[self._key(path)] = path.stat().st_mtime_ns

    def check(self, path: Path) -> str | None:
        """None when the file may be edited, else the reason it may not."""
        with self._lock:
            seen = self._seen.get(self._key(path))
        if seen is None:
            return f"Read {path} with the `read` tool before editing it."
        if path.stat().st_mtime_ns != seen:
            return f"{path} changed since you read it. Read it again, then edit."
        return None


FILE_STATE = FileState()


# ─── grep ───────────────────────────────────────────────────────────


class GrepTool(BaseTool):
    name = "grep"
    description = (
        "Search file CONTENTS with a regular expression (like ripgrep). Use it "
        "to find where a word, function, setting or error message appears. "
        "output_mode: 'content' (matching lines with file:line, default), "
        "'files' (just file paths), 'count' (matches per file). Narrow with "
        "`glob` (e.g. '*.py') and `path`. Skips .git, node_modules, build "
        "folders and binary files. For finding files by NAME use `glob`."
    )
    risk = RiskLevel.SAFE

    def run(
        self,
        pattern: str,
        path: str = ".",
        glob: str = "",
        ignore_case: bool = False,
        output_mode: str = "content",
        context: int = 0,
        max_results: int = 200,
    ) -> str:
        root = resolve_user_path(path or ".")
        if not root.exists():
            return f"Path not found: {root}"
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            return f"Invalid regex {pattern!r}: {e}"
        mode = output_mode if output_mode in ("content", "files", "count") else "content"
        context = max(0, min(int(context or 0), 10))
        max_results = max(1, min(int(max_results or 200), 2000))

        if shutil.which("rg"):
            out = self._ripgrep(pattern, root, glob, ignore_case, mode, context, max_results)
            if out is not None:
                return out
        return self._python(regex, root, glob, mode, context, max_results)

    def _ripgrep(self, pattern, root, glob, ignore_case, mode, context, max_results):
        cmd = ["rg", "--no-heading", "--color", "never", "--max-filesize", "5M"]
        if ignore_case:
            cmd.append("-i")
        if glob:
            cmd += ["-g", glob]
        if mode == "files":
            cmd.append("-l")
        elif mode == "count":
            cmd.append("-c")
        else:
            cmd.append("-n")
            if context:
                cmd += ["-C", str(context)]
        cmd += ["-e", pattern, str(root)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode not in (0, 1):
            return None
        lines = proc.stdout.splitlines()
        if not lines:
            return f"No matches for {pattern!r} in {root}"
        extra = len(lines) - max_results
        body = "\n".join(lines[:max_results])
        if extra > 0:
            body += f"\n…[{extra} more lines — narrow with path/glob]"
        return _truncate(body)

    def _python(self, regex, root, glob, mode, context, max_results):
        out: list[str] = []
        files_hit = 0
        for f in _walk_files(root):
            if glob and not fnmatch.fnmatch(f.name, glob) and not fnmatch.fnmatch(str(f), glob):
                continue
            try:
                if f.stat().st_size > MAX_FILE_BYTES or _is_binary(f):
                    continue
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            hits = [i for i, line in enumerate(lines) if regex.search(line)]
            if not hits:
                continue
            files_hit += 1
            if mode == "files":
                out.append(str(f))
            elif mode == "count":
                out.append(f"{f}:{len(hits)}")
            else:
                shown: set[int] = set()
                for i in hits:
                    for j in range(max(0, i - context), min(len(lines), i + context + 1)):
                        if j in shown:
                            continue
                        shown.add(j)
                        sep = ":" if j == i else "-"
                        out.append(f"{f}{sep}{j + 1}{sep}{lines[j]}")
            if len(out) >= max_results:
                out = out[:max_results]
                out.append("…[result limit reached — narrow with path/glob]")
                break
        if not out:
            return f"No matches for {regex.pattern!r} in {root}"
        return _truncate("\n".join(out))

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression"},
                    "path": {"type": "string", "description": "File or folder (default: cwd)"},
                    "glob": {"type": "string", "description": "File filter, e.g. '*.py'"},
                    "ignore_case": {"type": "boolean", "description": "Case-insensitive"},
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files", "count"],
                        "description": "What to return (default content)",
                    },
                    "context": {
                        "type": "integer",
                        "description": "Lines of context around each match (content mode)",
                    },
                    "max_results": {"type": "integer", "description": "Default 200"},
                },
                "required": ["pattern"],
            },
        }


# ─── glob ───────────────────────────────────────────────────────────


class GlobTool(BaseTool):
    name = "glob"
    description = (
        "Find files by NAME pattern, newest first. Patterns: '*.pdf', "
        "'**/*.py' (recursive), 'reports/**/2026-*.xlsx'. Skips .git, "
        "node_modules and build folders. To search inside files use `grep`."
    )
    risk = RiskLevel.SAFE

    def run(self, pattern: str, path: str = ".", limit: int = 200) -> str:
        root = resolve_user_path(path or ".")
        if not root.is_dir():
            return f"Folder not found: {root}"
        limit = max(1, min(int(limit or 200), 2000))
        found: list[tuple[float, Path]] = []
        try:
            for p in root.glob(pattern):
                if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
                    continue
                if p.is_file():
                    try:
                        found.append((p.stat().st_mtime, p))
                    except OSError:
                        continue
        except (ValueError, OSError) as e:
            return f"Bad pattern {pattern!r}: {e}"
        if not found:
            return f"No files match {pattern!r} in {root}"
        found.sort(key=lambda item: item[0], reverse=True)
        lines = [str(p) for _, p in found[:limit]]
        if len(found) > limit:
            lines.append(f"…[{len(found) - limit} more files]")
        return _truncate("\n".join(lines))

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern"},
                    "path": {"type": "string", "description": "Folder to search (default cwd)"},
                    "limit": {"type": "integer", "description": "Max files (default 200)"},
                },
                "required": ["pattern"],
            },
        }


# ─── read ───────────────────────────────────────────────────────────


class ReadTool(BaseTool):
    name = "read"
    description = (
        "Read a TEXT file as numbered lines ('  12\\tline'). For big files "
        "pass offset (first line, 1-based) and limit (number of lines) to "
        "read in parts. Required before `edit`. For PDF/Word/Excel/images "
        "use the `document` tool."
    )
    risk = RiskLevel.SAFE

    def __init__(self, state: FileState | None = None):
        self._state = state or FILE_STATE

    def run(self, path: str, offset: int = 1, limit: int = 2000) -> str:
        p = resolve_user_path(path)
        if not p.is_file():
            return f"File not found: {p}"
        if p.stat().st_size > MAX_FILE_BYTES * 4:
            size = p.stat().st_size
            return f"{p} is {size:,} bytes — too large; use grep to find the part you need."
        if _is_binary(p):
            return f"{p} is not a text file. Use the `document` tool for PDF/Office/images."
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        offset = max(1, int(offset or 1))
        limit = max(1, min(int(limit or 2000), 5000))
        chunk = lines[offset - 1 : offset - 1 + limit]
        self._state.mark(p)
        if not chunk:
            return f"{p} has {len(lines)} lines; offset {offset} is past the end."
        width = len(str(offset + len(chunk) - 1))
        body = "\n".join(
            f"{str(n).rjust(width)}\t{line[:2000]}" for n, line in enumerate(chunk, start=offset)
        )
        tail = ""
        if offset - 1 + len(chunk) < len(lines):
            tail = (
                f"\n…[lines {offset + len(chunk)}–{len(lines)} not shown — "
                f"read again with offset={offset + len(chunk)}]"
            )
        return _truncate(body + tail)

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "offset": {"type": "integer", "description": "First line, 1-based"},
                    "limit": {"type": "integer", "description": "Lines to read (default 2000)"},
                },
                "required": ["path"],
            },
        }


# ─── edit ───────────────────────────────────────────────────────────


class EditTool(BaseTool):
    name = "edit"
    description = (
        "Change a text file by exact string replacement. `old_string` must be "
        "copied exactly from the file (without the line-number prefix that "
        "`read` shows) and must appear exactly once — include more "
        "surrounding lines to make it unique, or set replace_all=true. The "
        "file must be read with `read` first. To create a new file use "
        "`file_ops` action=write."
    )
    risk = RiskLevel.MODERATE

    def __init__(self, state: FileState | None = None):
        self._state = state or FILE_STATE

    def run(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        p = resolve_user_path(path)
        if not p.is_file():
            return f"File not found: {p}. To create a file use `file_ops` action=write."
        problem = self._state.check(p)
        if problem:
            return problem
        if old_string == new_string:
            return "old_string and new_string are the same — nothing to change."
        if not old_string:
            return "old_string is empty — copy the exact text to replace from the file."
        raw = p.read_bytes().decode("utf-8", errors="replace")
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n") if crlf else raw
        old = old_string.replace("\r\n", "\n")
        new = new_string.replace("\r\n", "\n")
        count = text.count(old)
        if count == 0:
            return (
                "old_string not found in the file. Re-read the lines and copy them "
                "exactly (spaces, quotes and line breaks matter)."
            )
        if count > 1 and not replace_all:
            return (
                f"old_string appears {count} times. Add surrounding lines to make it "
                "unique, or set replace_all=true."
            )
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        if crlf:
            updated = updated.replace("\n", "\r\n")
        p.write_bytes(updated.encode("utf-8"))
        self._state.mark(p)
        n = count if replace_all else 1
        return f"Edited {p}: replaced {n} occurrence{'s' if n > 1 else ''}."

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_string": {"type": "string", "description": "Exact text to replace"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence (default false)",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        }
