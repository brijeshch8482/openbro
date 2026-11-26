"""Memory manager - 3-tier system: working, session, long-term."""

import uuid
from collections import deque
from pathlib import Path

from openbro.llm.base import Message
from openbro.memory import store
from openbro.memory.instructions import find_project_root
from openbro.memory.notes import Note, NoteStore, build_note_store, memory_block
from openbro.memory.transcript import Transcript, TranscriptStore, build_transcript_store


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
        transcripts: TranscriptStore | None = None,
    ):
        self.user_id = user_id
        self.channel = channel
        self.session_id = session_id or self._new_session_id()
        self.working_size = working_size
        self._working: deque[dict] = deque(maxlen=working_size)

        # Initialize DB
        store.init_db()
        try:
            from openbro.utils.config import load_config

            self._config = load_config()
        except Exception:
            self._config = {}
        if transcripts is None:
            transcripts = build_transcript_store(self._config)
        self.transcripts = transcripts
        self._notes: NoteStore | None = None  # built on first use

    def _new_session_id(self) -> str:
        return str(uuid.uuid4())[:12]

    # === Tier 1: Working memory ===

    def add(self, role: str, content: str, persist: bool = True):
        """Add a plain text message to working memory and optionally persist it."""
        if persist:
            self.record(Message(role=role, content=content))
        else:
            self._working.append({"role": role, "content": content})

    def record(self, msg: Message) -> str:
        """Persist one full message (tool calls and tool results included)
        to the session transcript. Sets and returns `msg.id`."""
        self._working.append({"role": msg.role, "content": msg.content})
        uid = self.transcripts.append(self.session_id, self.user_id, self.channel, msg)
        msg.id = uid
        return uid

    def save_summary(self, summary: str, upto_uid: str | None) -> None:
        """Store the rolling conversation summary written by compaction."""
        self.transcripts.set_summary(self.session_id, self.user_id, summary, upto_uid)

    def load_transcript(self, session_id: str) -> Transcript:
        """Switch to `session_id` and return its summary + messages after it."""
        self.load_session(session_id)
        return self.transcripts.load(session_id, self.user_id)

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

    # === Tier 3: Long-term memories (notes) ===

    @property
    def notes(self) -> NoteStore:
        if self._notes is None:
            self._notes = build_note_store(self.user_id, self._config)
            self._migrate_facts()
        return self._notes

    def _migrate_facts(self) -> None:
        """One-time copy of old key/value facts into notes."""
        marker = self._notes.location() / ".facts-migrated"
        if marker.exists():
            return
        try:
            for f in store.list_facts(self.user_id):
                if self._notes.get(f["key"]) is None:
                    self._notes.save(_fact_note(f["key"], f["value"]))
            marker.write_text("facts copied into notes\n", encoding="utf-8")
        except Exception:
            pass  # retried next start; old facts stay readable meanwhile

    def save_note(self, note: Note) -> Note:
        return self.notes.save(note)

    def remember(self, key: str, value: str, category: str = "general"):
        """Store a fact about the user (a `user` note named after the key)."""
        self.notes.save(_fact_note(key, value))

    def recall(self, key: str) -> str | None:
        note = self.notes.get(key)
        return note.body if note else None

    def forget(self, key: str) -> bool:
        return self.notes.delete(key)

    def all_facts(self, category: str | None = None) -> list[dict]:
        """Every note as a key/value row (kept for the `memory` command)."""
        return [
            {"key": n.name, "value": n.body, "category": n.type, "updated_at": n.updated}
            for n in sorted(self.notes.all(), key=lambda n: n.updated, reverse=True)
            if category is None or n.type == category
        ]

    def search(self, query: str) -> dict:
        """Search memories and past conversation messages."""
        hits = self.notes.search(query)
        return {
            "facts": [{"key": n.name, "value": n.body, "category": n.type} for n, _ in hits],
            "notes": [n for n, _ in hits],
            "messages": store.search_messages(query, self.user_id, limit=10),
        }

    def memory_block(self, query: str = "", cwd: Path | None = None) -> str:
        """Memories that matter for this turn, within the token budget."""
        root = find_project_root(cwd or Path.cwd())
        budget = int(
            ((self._config or {}).get("memory", {}) or {}).get("index_budget_tokens", 1500)
        )
        return memory_block(self.notes.all(), query, str(root) if root else None, budget)

    def context_prompt(self) -> str:
        return self.memory_block("")

    def stats(self) -> dict:
        stats = store.get_stats()
        stats["facts"] = len(self.notes.all())
        return stats


def _fact_note(key: str, value: str) -> Note:
    return Note(name=key, description=f"{key}: {value}"[:200], type="user", body=value)
