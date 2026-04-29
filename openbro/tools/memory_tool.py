"""Memory tool - remember, recall, forget, and search user facts."""

from openbro.memory import MemoryManager
from openbro.tools.base import BaseTool, RiskLevel


class MemoryTool(BaseTool):
    name = "memory"
    description = (
        "Remember facts about the user (name, preferences, custom info), "
        "recall them later, or search past conversations and stored facts."
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
        key: str = "",
        value: str = "",
        category: str = "general",
        query: str = "",
    ) -> str:
        action = action.lower().strip()
        m = self._mgr()

        if action == "remember":
            if not key or not value:
                return "Both 'key' and 'value' required for remember"
            m.remember(key, value, category=category)
            return f"Got it. Yaad rakh liya: {key} = {value}"

        elif action == "recall":
            if not key:
                return "'key' required for recall"
            val = m.recall(key)
            return f"{key} = {val}" if val else f"Kuch yaad nahi '{key}' ke baare me"

        elif action == "forget":
            if not key:
                return "'key' required for forget"
            removed = m.forget(key)
            return f"Forgot: {key}" if removed else f"Nothing to forget for: {key}"

        elif action == "list":
            facts = m.all_facts(category=category if category != "general" else None)
            if not facts:
                return "No facts stored yet"
            lines = [f"  - {f['key']}: {f['value']} [{f['category']}]" for f in facts[:30]]
            return "Stored facts:\n" + "\n".join(lines)

        elif action == "search":
            if not query:
                return "'query' required for search"
            results = m.search(query)
            facts = results["facts"]
            msgs = results["messages"]

            parts = []
            if facts:
                parts.append("Facts:")
                parts.extend(f"  - {f['key']}: {f['value']}" for f in facts[:10])
            if msgs:
                parts.append("\nPast messages:")
                for msg in msgs[:5]:
                    preview = msg["content"][:100]
                    parts.append(f"  [{msg['role']}] {preview}")
            return "\n".join(parts) if parts else f"Nothing found for '{query}'"

        else:
            return f"Unknown action: {action}. Available: remember, recall, forget, list, search"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["remember", "recall", "forget", "list", "search"],
                        "description": "Memory action to perform",
                    },
                    "key": {
                        "type": "string",
                        "description": (
                            "Fact key (e.g. 'name', 'birthday', 'favorite_color'). "
                            "Required for remember, recall, forget."
                        ),
                    },
                    "value": {
                        "type": "string",
                        "description": "Fact value (required for remember)",
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "Category for organizing facts: general, personal, "
                            "work, preferences, etc."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (for search action)",
                    },
                },
                "required": ["action"],
            },
        }
