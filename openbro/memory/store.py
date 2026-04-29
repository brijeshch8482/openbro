"""SQLite-backed persistent memory store."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from openbro.utils.storage import get_storage_paths


def _db_path() -> Path:
    paths = get_storage_paths()
    db_file = Path(paths["memory"]) / "memory.db"
    return db_file


@contextmanager
def _connect():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with _connect() as conn:
        # Facts: user_id -> key -> value (e.g., user's name, preferences)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT 'default',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, key)
            )
            """
        )
        # Conversation history: long-term archive of messages
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT 'default',
                session_id TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'cli',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        # Sessions: track open conversations per user
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                channel TEXT NOT NULL DEFAULT 'cli',
                started_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
            """
        )
        # Indexes
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, timestamp)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id)")


# === Facts (long-term structured memory) ===


def set_fact(key: str, value: str, user_id: str = "default", category: str = "general"):
    """Store or update a fact for a user."""
    init_db()
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO facts (user_id, key, value, category, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET
                value = excluded.value,
                category = excluded.category,
                updated_at = excluded.updated_at
            """,
            (user_id, key, value, category, now, now),
        )


def get_fact(key: str, user_id: str = "default") -> str | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM facts WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
        return row["value"] if row else None


def delete_fact(key: str, user_id: str = "default") -> bool:
    init_db()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM facts WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        return cursor.rowcount > 0


def list_facts(user_id: str = "default", category: str | None = None) -> list[dict]:
    init_db()
    with _connect() as conn:
        if category:
            rows = conn.execute(
                """
                SELECT key, value, category, updated_at FROM facts
                WHERE user_id = ? AND category = ?
                ORDER BY updated_at DESC
                """,
                (user_id, category),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT key, value, category, updated_at FROM facts
                WHERE user_id = ?
                ORDER BY updated_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def search_facts(query: str, user_id: str = "default") -> list[dict]:
    """Simple keyword search across facts."""
    init_db()
    with _connect() as conn:
        pattern = f"%{query}%"
        rows = conn.execute(
            """
            SELECT key, value, category FROM facts
            WHERE user_id = ? AND (key LIKE ? OR value LIKE ?)
            ORDER BY updated_at DESC LIMIT 50
            """,
            (user_id, pattern, pattern),
        ).fetchall()
        return [dict(r) for r in rows]


# === Conversations (long-term chat archive) ===


def save_message(
    role: str,
    content: str,
    session_id: str,
    user_id: str = "default",
    channel: str = "cli",
):
    init_db()
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations (user_id, session_id, channel, role, content, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, session_id, channel, role, content, now),
        )
        # Update session activity
        conn.execute(
            """
            INSERT INTO sessions (session_id, user_id, channel, started_at, last_activity)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET last_activity = excluded.last_activity
            """,
            (session_id, user_id, channel, now, now),
        )


def get_recent_messages(
    user_id: str = "default",
    session_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    init_db()
    with _connect() as conn:
        if session_id:
            rows = conn.execute(
                """
                SELECT role, content, timestamp FROM conversations
                WHERE user_id = ? AND session_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT role, content, timestamp FROM conversations
                WHERE user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        # Return in chronological order
        return [dict(r) for r in reversed(rows)]


def search_messages(
    query: str,
    user_id: str = "default",
    limit: int = 20,
) -> list[dict]:
    init_db()
    with _connect() as conn:
        pattern = f"%{query}%"
        rows = conn.execute(
            """
            SELECT role, content, timestamp, session_id FROM conversations
            WHERE user_id = ? AND content LIKE ?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# === Sessions ===


def list_sessions(user_id: str = "default", limit: int = 20) -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT session_id, channel, started_at, last_activity, metadata
            FROM sessions
            WHERE user_id = ?
            ORDER BY last_activity DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d.get("metadata") or "{}")
            except json.JSONDecodeError:
                d["metadata"] = {}
            result.append(d)
        return result


def get_stats() -> dict:
    init_db()
    with _connect() as conn:
        facts_count = conn.execute("SELECT COUNT(*) as c FROM facts").fetchone()["c"]
        msgs_count = conn.execute("SELECT COUNT(*) as c FROM conversations").fetchone()["c"]
        sessions_count = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        return {
            "facts": facts_count,
            "messages": msgs_count,
            "sessions": sessions_count,
        }
