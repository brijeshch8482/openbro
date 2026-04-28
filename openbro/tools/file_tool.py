"""File operations tool."""

from pathlib import Path

from openbro.tools.base import BaseTool


class FileTool(BaseTool):
    name = "file_ops"
    description = "Read, write, list, and search files on the system"

    def run(self, action: str, path: str = ".", content: str = "", pattern: str = "") -> str:
        path = Path(path).expanduser()

        if action == "read":
            if not path.exists():
                return f"File not found: {path}"
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

        else:
            return f"Unknown action: {action}. Available: read, write, list, search"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "write", "list", "search"],
                        "description": "Action to perform",
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
