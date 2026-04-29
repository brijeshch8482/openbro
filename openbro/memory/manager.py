"""Memory manager - 3-tier system: working, session, long-term."""

import uuid
from collections import deque

from openbro.memory import store


class MemoryManager:
    """3-tier memory system.

    Tier 1 (Working): in-memory deque of last N messages, fast access for the LLM.
    Tier 2 (Session): full conversation persisted to SQLite, scoped to session_id.
    Tier 3 (Long-term): facts table - user preferences, name, custom info.
    """

    def __init__(
        self,
        user_id: str = "default",
        channel: str = "cli",
        session_id: str | None = None,
        working_size: int = 30,
    ):
        self.user_id = user_id
        self.channel = channel
        self.session_id = session_id or self._new_session_id()
        self.working_size = working_size
        self._working: deque[dict] = deque(maxlen=working_size)

        # Initialize DB
        store.init_db()

    def _new_session_id(self) -> str:
        return str(uuid.uuid4())[:12]

    # === Tier 1: Working memory ===

    def add(self, role: str, content: str, persist: bool = True):
        """Add a message to working memory and optionally persist to session store."""
        self._working.append({"role": role, "content": content})
        if persist:
            store.save_message(
                role=role,
                content=content,
                session_id=self.session_id,
                user_id=self.user_id,
                channel=self.channel,
            )

    def working(self) -> list[dict]:
        return list(self._working)

    def clear_working(self):
        self._working.clear()

    # === Tier 2: Session history ===

    def session_history(self, limit: int = 100) -> list[dict]:
        return store.get_recent_messages(
            user_id=self.user_id,
            session_id=self.session_id,
            limit=limit,
        )

    def load_session(self, session_id: str):
        """Load an existing session's recent history into working memory."""
        self.session_id = session_id
        msgs = store.get_recent_messages(
            user_id=self.user_id,
            session_id=session_id,
            limit=self.working_size,
        )
        self._working.clear()
        for m in msgs:
            self._working.append({"role": m["role"], "content": m["content"]})

    def list_sessions(self) -> list[dict]:
        return store.list_sessions(self.user_id)

    # === Tier 3: Long-term facts ===

    def remember(self, key: str, value: str, category: str = "general"):
        """Store a long-term fact about the user."""
        store.set_fact(key, value, user_id=self.user_id, category=category)

    def recall(self, key: str) -> str | None:
        return store.get_fact(key, user_id=self.user_id)

    def forget(self, key: str) -> bool:
        return store.delete_fact(key, user_id=self.user_id)

    def all_facts(self, category: str | None = None) -> list[dict]:
        return store.list_facts(self.user_id, category=category)

    def search(self, query: str) -> dict:
        """Search across both facts and conversation history."""
        return {
            "facts": store.search_facts(query, self.user_id),
            "messages": store.search_messages(query, self.user_id, limit=10),
        }

    def context_prompt(self) -> str:
        """Generate a context block to inject into system prompt."""
        facts = self.all_facts()
        if not facts:
            return ""

        lines = ["Things you know about this user:"]
        for f in facts[:20]:
            lines.append(f"- {f['key']}: {f['value']}")
        return "\n".join(lines)

    def stats(self) -> dict:
        return store.get_stats()
