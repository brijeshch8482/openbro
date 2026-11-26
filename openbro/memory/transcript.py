"""Lossless session transcripts (harness piece 2, memory L3).

Every message the agent appends — user, assistant (with its tool calls)
and tool results — is recorded, so `--resume` brings back exactly what the
model saw. The rolling conversation summary written by compaction is kept
on the session row together with the uid of the last message it covers.

Storage sits behind `TranscriptStore` so the same calls can later go to an
OpenBro server (`storage.backend: remote`). The local backend is SQLite in
the existing `memory.db`.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from openbro.llm.base import Message
from openbro.memory import store

TRANSCRIPT_SCHEMA = 2


@dataclass
class Transcript:
    """A session as it should be restored: summary + messages after it."""

    session_id: str
    summary: str | None = None
    summary_upto: str | None = None
    messages: list[Message] = field(default_factory=list)


class TranscriptStore(Protocol):
    def append(self, session_id: str, owner: str, channel: str, msg: Message) -> str: ...

    def load(self, session_id: str, owner: str) -> Transcript: ...

    def set_summary(
        self, session_id: str, owner: str, summary: str, upto_uid: str | None
    ) -> None: ...


class SQLiteTranscriptStore:
    """Local backend. Shares `memory.db` with the facts/sessions tables;
    `conversations.user_id` is the owner column."""

    def __init__(self) -> None:
        store.init_db()
        self._migrate()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(store._db_path()))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(conversations)")}
            for name in ("uid", "tool_calls", "tool_call_id"):
                if name not in cols:
                    conn.execute(f"ALTER TABLE conversations ADD COLUMN {name} TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id, id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_uid ON conversations(uid)")
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('transcript_schema', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(TRANSCRIPT_SCHEMA),),
            )

    # ─── writes ──────────────────────────────────────────────────────

    def append(self, session_id: str, owner: str, channel: str, msg: Message) -> str:
        uid = msg.id or uuid.uuid4().hex
        now = datetime.now().isoformat()
        tool_calls = json.dumps(msg.tool_calls, ensure_ascii=False) if msg.tool_calls else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations
                    (user_id, session_id, channel, role, content, timestamp,
                     uid, tool_calls, tool_call_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner,
                    session_id,
                    channel,
                    msg.role,
                    msg.content or "",
                    now,
                    uid,
                    tool_calls,
                    msg.tool_call_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO sessions (session_id, user_id, channel, started_at, last_activity)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET last_activity = excluded.last_activity
                """,
                (session_id, owner, channel, now, now),
            )
        return uid

    def set_summary(self, session_id: str, owner: str, summary: str, upto_uid: str | None) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT metadata FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            meta = _load_meta(row["metadata"] if row else None)
            meta["summary"] = summary
            meta["summary_upto"] = upto_uid
            if row:
                conn.execute(
                    "UPDATE sessions SET metadata = ? WHERE session_id = ?",
                    (json.dumps(meta, ensure_ascii=False), session_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO sessions
                        (session_id, user_id, channel, started_at, last_activity, metadata)
                    VALUES (?, ?, 'cli', ?, ?, ?)
                    """,
                    (session_id, owner, now, now, json.dumps(meta, ensure_ascii=False)),
                )

    # ─── reads ───────────────────────────────────────────────────────

    def load(self, session_id: str, owner: str) -> Transcript:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT metadata FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            meta = _load_meta(row["metadata"] if row else None)
            summary = meta.get("summary") or None
            upto = meta.get("summary_upto") or None

            after_id = 0
            if summary and upto:
                hit = conn.execute(
                    "SELECT id FROM conversations WHERE session_id = ? AND uid = ?",
                    (session_id, upto),
                ).fetchone()
                if hit:
                    after_id = hit["id"]
            rows = conn.execute(
                """
                SELECT role, content, uid, tool_calls, tool_call_id FROM conversations
                WHERE session_id = ? AND user_id = ? AND id > ?
                ORDER BY id
                """,
                (session_id, owner, after_id),
            ).fetchall()

        messages = []
        for r in rows:
            try:
                calls = json.loads(r["tool_calls"]) if r["tool_calls"] else []
            except (TypeError, ValueError):
                calls = []
            messages.append(
                Message(
                    role=r["role"],
                    content=r["content"] or "",
                    tool_calls=calls if isinstance(calls, list) else [],
                    tool_call_id=r["tool_call_id"],
                    id=r["uid"],
                )
            )
        return Transcript(
            session_id=session_id, summary=summary, summary_upto=upto, messages=messages
        )


class InMemoryTranscriptStore:
    """Throwaway transcript for sub-agents — nothing reaches the database."""

    def __init__(self) -> None:
        self._messages: dict[str, list[Message]] = {}
        self._summaries: dict[str, tuple[str, str | None]] = {}

    def append(self, session_id: str, owner: str, channel: str, msg: Message) -> str:
        uid = msg.id or uuid.uuid4().hex
        self._messages.setdefault(session_id, []).append(msg)
        return uid

    def load(self, session_id: str, owner: str) -> Transcript:
        summary, upto = self._summaries.get(session_id, (None, None))
        return Transcript(
            session_id=session_id,
            summary=summary,
            summary_upto=upto,
            messages=list(self._messages.get(session_id, [])),
        )

    def set_summary(self, session_id: str, owner: str, summary: str, upto_uid: str | None) -> None:
        self._summaries[session_id] = (summary, upto_uid)


def _load_meta(raw) -> dict:
    try:
        meta = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def build_transcript_store(config: dict | None = None) -> TranscriptStore:
    """Pick the transcript backend from `storage.backend`."""
    backend = ((config or {}).get("storage", {}) or {}).get("backend", "local")
    if backend == "local":
        return SQLiteTranscriptStore()
    if backend == "remote":
        raise NotImplementedError(
            "storage.backend 'remote' needs the OpenBro server (planned for v2). "
            "Set storage.backend: local for now."
        )
    raise ValueError(f"Unknown storage.backend: {backend!r} (expected 'local' or 'remote')")
