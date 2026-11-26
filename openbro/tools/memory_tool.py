"""Memory tool — OpenBro's long-term memories (one note per memory).

Raw operations only: save / read / delete / list / search. The model
decides what is worth remembering (see the memory principle in the system
prompt). Older actions (remember / recall / forget) still work and map
onto notes.
"""

from openbro.memory import MemoryManager
from openbro.memory.notes import NOTE_TYPES, Note
from openbro.tools.base import BaseTool, RiskLevel


class MemoryTool(BaseTool):
    name = "memory"
    description = (
        "OpenBro's long-term memory about the user and their work, kept "
        "across sessions. One memory = one note with a short name, a one-line "
        "description, a type and a body.\n"
        "Types: user (who they are, preferences), feedback (how they want you "
        "to work — corrections and confirmed approaches, with why), project "
        "(ongoing work, decisions, deadlines), reference (where things live: "
        "paths, URLs, accounts).\n"
        "Actions: save (create or update by name), read, delete, list, search "
        "(memories + past conversations).\n"
        "Save when the user tells you something lasting about themselves, "
        "corrects you, or a durable decision appears. Update an existing note "
        "instead of creating a duplicate. Never store passwords or secrets.\n"
        "NOT for Windows Sticky Notes (use `sticky_notes`), NOT for files."
    )
    risk = RiskLevel.SAFE

    def __init__(self, manager: MemoryManager | None = None):
        self._manager = manager

    def _mgr(self) -> MemoryManager:
        if self._manager is None:
            self._manager = MemoryManager()
        return self._manager

    def run(
        self,
        action: str,
        name: str = "",
        description: str = "",
        type: str = "",
        body: str = "",
        scope: str = "global",
        query: str = "",
        key: str = "",
        value: str = "",
        category: str = "general",
    ) -> str:
        action = (action or "").lower().strip()
        m = self._mgr()

        # Older action names → note operations.
        if action == "remember":
            action, name = "save", name or key
            body = body or value
            description = description or f"{key}: {value}"
        elif action in ("recall", "forget"):
            name = name or key
            action = "read" if action == "recall" else "delete"

        if action == "save":
            type = type or "user"
            if not name or not (body or description):
                return "save needs 'name' and 'body' (plus a one-line 'description')"
            if type not in NOTE_TYPES:
                return f"type must be one of: {', '.join(NOTE_TYPES)}"
            if scope == "project":
                from pathlib import Path

                from openbro.memory.instructions import find_project_root

                root = find_project_root(Path.cwd())
                scope = f"project:{root}" if root else "global"
            existed = m.notes.get(name) is not None
            note = m.save_note(
                Note(
                    name=name,
                    description=(description or body.splitlines()[0])[:200],
                    type=type,
                    body=body or description,
                    scope=scope or "global",
                )
            )
            return f"{'Updated' if existed else 'Saved'} memory '{note.name}' ({note.type})"

        if action == "read":
            if not name:
                return "read needs 'name'"
            note = m.notes.get(name)
            if note is None:
                return f"No memory named '{name}'. Use action=list or action=search."
            return (
                f"{note.name} ({note.type}, scope {note.scope}, updated {note.updated})\n"
                f"{note.description}\n\n{note.body}"
            )

        if action == "delete":
            if not name:
                return "delete needs 'name'"
            return f"Deleted memory '{name}'" if m.forget(name) else f"No memory named '{name}'"

        if action == "list":
            notes = m.notes.all()
            if type in NOTE_TYPES:
                notes = [n for n in notes if n.type == type]
            if not notes:
                return "No memories saved yet"
            return "Memories:\n" + "\n".join(n.index_line() for n in notes)

        if action == "search":
            if not query:
                return "search needs 'query'"
            results = m.search(query)
            parts = []
            if results["notes"]:
                parts.append("Memories:")
                for n in results["notes"]:
                    snippet = " ".join(n.body.split())[:160]
                    parts.append(f"{n.index_line()}\n    {snippet}")
            if results["messages"]:
                parts.append("Past conversation messages:")
                for msg in results["messages"][:6]:
                    preview = " ".join((msg["content"] or "").split())[:160]
                    parts.append(f"  [{msg['timestamp'][:10]} {msg['role']}] {preview}")
            return "\n".join(parts) if parts else f"Nothing found for '{query}'"

        return f"Unknown action: {action}. Available: save, read, delete, list, search"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["save", "read", "delete", "list", "search"],
                        "description": "What to do",
                    },
                    "name": {
                        "type": "string",
                        "description": "Short kebab-case name, e.g. 'prefers-short-replies'",
                    },
                    "description": {
                        "type": "string",
                        "description": "One line saying what this memory is (save)",
                    },
                    "type": {
                        "type": "string",
                        "enum": list(NOTE_TYPES),
                        "description": "Memory type (save; filter for list)",
                    },
                    "body": {
                        "type": "string",
                        "description": (
                            "The memory itself (save). For feedback/project add "
                            "'Why:' and 'How to apply:' lines."
                        ),
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "project"],
                        "description": "global (default) or only for the current project (save)",
                    },
                    "query": {"type": "string", "description": "Search text (search)"},
                },
                "required": ["action"],
            },
        }
