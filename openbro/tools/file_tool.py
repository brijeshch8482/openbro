"""File operations tool."""

import os
import platform
import subprocess

from openbro.tools.base import BaseTool, RiskLevel
from openbro.utils.paths import resolve_user_path


class FileTool(BaseTool):
    name = "file_ops"
    description = (
        "Read, write, list, search, or open files on the system. "
        "Use 'open' to launch a file (PDF, image, video, anything) in the "
        "user's default app. 'read' auto-handles text AND binary formats "
        "(PDFs/docx/xlsx/images/audio/HTML/zip get dispatched to the "
        "`document` backend). For a specific backend or audio "
        "transcription options, call `document` directly."
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
            # Launch in default app. Real-user gap: 'open aadhar.pdf' would
            # crash with 'Unknown action: open' because file_ops only had
            # read/write/list/search. The model could call word.open but
            # that only works for .docx — for arbitrary files we need a
            # generic launcher. PDF/image/video/zip etc. all work via
            # os.startfile on Windows.
            if not path.exists():
                return f"File not found: {path}"
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
