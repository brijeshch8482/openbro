"""File operations tool."""

import os
import platform
import subprocess
from pathlib import Path

from openbro.tools.base import BaseTool, RiskLevel
from openbro.utils.paths import resolve_user_path

# Where to look when user gives a filename without a directory ("open aadhar"
# or "open T&P fees"). Walked in order; first hit wins. Keeps the agent from
# bailing out and asking the user "kya extension hai?" when the answer is on
# the filesystem already.
_COMMON_SEARCH_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "OneDrive" / "Desktop",
    Path.home() / "Documents",
    Path.home() / "OneDrive" / "Documents",
    Path.home() / "Downloads",
    Path.home() / "OneDrive" / "Downloads",
]


def _fuzzy_find(path: Path, max_results: int = 25) -> list[Path]:
    """Find files matching `path` when the literal path doesn't exist.

    Strategy:
      1. If `path` has a parent that exists, glob `<stem>*` in that parent.
         Handles 'D:/T&P fees' → 'D:/T&P fees.pdf'.
      2. Otherwise, walk the common user roots (Desktop / Documents /
         Downloads on both system + OneDrive copies) looking for files whose
         stem CONTAINS the requested basename. Substring match so 'oops' hits
         'oops_final_v2.pdf'.

    Returns at most `max_results` paths sorted by closeness (exact stem
    match first). Empty list = no candidates.
    """
    stem_query = path.stem.lower()
    if not stem_query:
        return []

    # Step 1: parent-dir glob (cheap, precise).
    if path.parent and path.parent.exists() and path.parent != path:
        try:
            candidates = [
                p for p in path.parent.iterdir() if p.is_file() and p.stem.lower() == stem_query
            ]
            if candidates:
                return candidates[:max_results]
            # No exact-stem match — try wildcard (T&P fees → T&P fees v2.pdf)
            wildcard = list(path.parent.glob(f"{path.stem}*"))
            wildcard = [p for p in wildcard if p.is_file()]
            if wildcard:
                return wildcard[:max_results]
        except (PermissionError, OSError):
            pass

    # Step 2: walk known user roots, substring match on stem.
    hits: list[Path] = []
    for root in _COMMON_SEARCH_ROOTS:
        if not root.exists():
            continue
        try:
            for entry in root.iterdir():
                if entry.is_file() and stem_query in entry.stem.lower():
                    hits.append(entry)
                    if len(hits) >= max_results:
                        break
        except (PermissionError, OSError):
            continue
        if len(hits) >= max_results:
            break

    # Sort: exact stem match first, then alphabetical.
    hits.sort(key=lambda p: (p.stem.lower() != stem_query, p.name.lower()))
    return hits[:max_results]


class FileTool(BaseTool):
    name = "file_ops"
    description = (
        "Read, write, list, search, or open files on the system. "
        "'open' launches a file (PDF, image, video, anything) in the user's "
        "default app and FUZZY-MATCHES by basename when the exact path is "
        "missing — pass 'T&P fees' and it finds 'T&P fees.pdf' on Desktop/"
        "Documents/Downloads (system + OneDrive). DON'T ask the user for the "
        "extension before trying — open just figures it out. 'read' "
        "auto-handles text AND binary formats (PDFs/docx/xlsx/images/audio/"
        "HTML/zip get dispatched to the `document` backend). For a specific "
        "backend or audio transcription options, call `document` directly."
    )
    risk = RiskLevel.MODERATE

    def run(self, action: str, path: str = ".", content: str = "", pattern: str = "") -> str:
        # OneDrive-aware: '~/Desktop' resolves to the real Desktop the user
        # sees in Explorer (which is under OneDrive on most Windows installs
        # with sync enabled).
        path = resolve_user_path(path)

        if action == "read":
            if not path.exists():
                return f"File not found: {path}"
            # Non-text formats (PDF, image, audio, docx, etc.) explode or
            # return garbage when read as text. Delegate them to the
            # document tool which dispatches by extension. file_ops still
            # owns plain text / code / unknown extensions for the common
            # case (`read foo.py`, `read notes.txt`).
            from openbro.tools.document_tool import (
                AUDIO_EXTS,
                DOCX_EXTS,
                EXCEL_EXTS,
                HTML_EXTS,
                IMAGE_EXTS,
                PDF_EXTS,
                ZIP_EXTS,
                DocumentTool,
            )

            ext = path.suffix.lower()
            if ext in (
                PDF_EXTS | DOCX_EXTS | EXCEL_EXTS | IMAGE_EXTS | AUDIO_EXTS | HTML_EXTS | ZIP_EXTS
            ):
                return DocumentTool().run(action="read", file=str(path))
            return path.read_text(encoding="utf-8", errors="replace")[:10000]

        elif action == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"Written to {path}"

        elif action == "list":
            if not path.exists():
                return f"Directory not found: {path}"
            entries = []
            for item in sorted(path.iterdir()):
                prefix = "DIR " if item.is_dir() else "FILE"
                entries.append(f"  {prefix}  {item.name}")
            return f"Contents of {path}:\n" + "\n".join(entries) if entries else "Empty directory"

        elif action == "search":
            if not pattern:
                return "Pattern required for search"
            results = list(path.rglob(pattern))[:50]
            if not results:
                return f"No files matching '{pattern}' in {path}"
            return "\n".join(str(r) for r in results)

        elif action == "open":
            # If the literal path doesn't exist, fuzzy-search before giving
            # up. User says "open T&P fees" — file_ops should find
            # "T&P fees.pdf" and open it, not bail out asking "kya extension
            # hai?". Real captured failure: user said the agent kept
            # demanding ".pdf" when the unique match was right there.
            if not path.exists():
                matches = _fuzzy_find(path)
                if len(matches) == 1:
                    path = matches[0]
                elif len(matches) > 1:
                    listing = "\n".join(f"  {m}" for m in matches[:10])
                    return (
                        f"Multiple files match '{path.name}':\n{listing}\n"
                        + (f"  ... (+{len(matches) - 10} more)\n" if len(matches) > 10 else "")
                        + "Call file_ops open again with the full path "
                        "(including extension) of the one you want."
                    )
                else:
                    return (
                        f"File not found: {path}. No fuzzy matches in parent "
                        "dir or common locations (Desktop/Documents/Downloads, "
                        "including OneDrive copies). Try file_ops search with a "
                        "broader pattern, or pass the absolute path."
                    )

            try:
                if platform.system() == "Windows":
                    os.startfile(str(path))  # type: ignore[attr-defined]
                elif platform.system() == "Darwin":
                    subprocess.run(["open", str(path)], check=False)
                else:
                    subprocess.run(["xdg-open", str(path)], check=False)
                return f"Opened {path} in default app"
            except Exception as e:
                return f"Failed to open: {e}"

        else:
            return f"Unknown action: {action}. Available: read, write, list, search, open"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "write", "list", "search", "open"],
                        "description": (
                            "read=text contents, write=create/overwrite, "
                            "list=directory entries, search=glob pattern, "
                            "open=launch in default app (PDF/image/video/anything)."
                        ),
                    },
                    "path": {"type": "string", "description": "File or directory path"},
                    "content": {
                        "type": "string",
                        "description": "Content to write (for write action)",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (for search action)",
                    },
                },
                "required": ["action"],
            },
        }
