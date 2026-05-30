"""Session memory — durable goal + milestone tracking across REPL turns.

Recap (openbro/core/recap.py) reads the in-flight chat history and
synthesizes a one-line 'where are we' summary. That's per-session and
disappears on exit. SessionMemory is the persistent layer: when the
agent detects a goal-setting turn or a milestone, it writes a row to
SQLite so a future `openbro --resume` picks up *exactly* where the
last session left off — with the same goal context the assistant had
been working toward.

Schema:
  session_goals(id, session_id, user_id, text, created_at, completed_at)
  session_milestones(id, session_id, user_id, text, created_at, kind)
    kind ∈ {'success', 'failure', 'note'}

The recap synthesizer pulls from both tables before falling back to
the in-memory history scan, so persisted context wins.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from openbro.utils.storage import get_storage_paths


@dataclass
class Goal:
    id: int
    session_id: str
    user_id: str
    text: str
    created_at: float
    completed_at: float | None = None


@dataclass
class Milestone:
    id: int
    session_id: str
    user_id: str
    text: str
    created_at: float
    kind: str = "note"  # success | failure | note


def _db_path() -> Path:
    """SessionMemory shares the same SQLite file the regular memory
    manager already uses — avoids creating a second DB / migration
    path. Tables are created on first use."""
    paths = get_storage_paths()
    db_file = Path(paths["memory"]) / "memory.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return db_file


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE — safe to call on every access."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS session_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at REAL NOT NULL,
            completed_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_goals_session
            ON session_goals(session_id, completed_at);
        CREATE INDEX IF NOT EXISTS idx_goals_user
            ON session_goals(user_id);

        CREATE TABLE IF NOT EXISTS session_milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at REAL NOT NULL,
            kind TEXT NOT NULL DEFAULT 'note'
        );
        CREATE INDEX IF NOT EXISTS idx_milestones_session
            ON session_milestones(session_id, created_at);
        """
    )
    conn.commit()


def _connect() -> sqlite3.Connection:
    """Open a connection with sensible defaults. The caller closes it."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


# ─── Goals ─────────────────────────────────────────────────────────────


def record_goal(
    session_id: str,
    user_id: str,
    text: str,
) -> Goal | None:
    """Persist a goal. De-duplicates: if the same text is already an
    OPEN goal for this session, no insert."""
    text = text.strip()
    if not text:
        return None
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id, created_at FROM session_goals "
            "WHERE session_id = ? AND text = ? AND completed_at IS NULL",
            (session_id, text),
        ).fetchone()
        if existing:
            return Goal(
                id=existing["id"],
                session_id=session_id,
                user_id=user_id,
                text=text,
                created_at=existing["created_at"],
            )
        now = time.time()
        cur = conn.execute(
            "INSERT INTO session_goals (session_id, user_id, text, created_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, text, now),
        )
        conn.commit()
        return Goal(
            id=cur.lastrowid or 0,
            session_id=session_id,
            user_id=user_id,
            text=text,
            created_at=now,
        )
    finally:
        conn.close()


def complete_goal(session_id: str, text: str) -> bool:
    """Mark a goal completed (by text match within the session)."""
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE session_goals SET completed_at = ? "
            "WHERE session_id = ? AND text = ? AND completed_at IS NULL",
            (time.time(), session_id, text.strip()),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def open_goals(session_id: str, limit: int = 5) -> list[Goal]:
    """Open (uncompleted) goals for a session, newest first."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, session_id, user_id, text, created_at, completed_at "
            "FROM session_goals "
            "WHERE session_id = ? AND completed_at IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [Goal(**dict(r)) for r in rows]
    finally:
        conn.close()


def recent_goals(user_id: str, limit: int = 10) -> list[Goal]:
    """Cross-session: most recent goals for the user (any session,
    open or completed). Used when the user resumes after a break to
    show what they were working on last."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, session_id, user_id, text, created_at, completed_at "
            "FROM session_goals "
            "WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [Goal(**dict(r)) for r in rows]
    finally:
        conn.close()


# ─── Milestones ────────────────────────────────────────────────────────


def record_milestone(
    session_id: str,
    user_id: str,
    text: str,
    kind: str = "note",
) -> Milestone | None:
    """Log a milestone. No dedup (the same outcome can legitimately
    repeat — 'tests pass' on commit N then N+1)."""
    text = text.strip()
    if not text:
        return None
    if kind not in ("success", "failure", "note"):
        kind = "note"
    conn = _connect()
    try:
        now = time.time()
        cur = conn.execute(
            "INSERT INTO session_milestones "
            "(session_id, user_id, text, created_at, kind) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, text, now, kind),
        )
        conn.commit()
        return Milestone(
            id=cur.lastrowid or 0,
            session_id=session_id,
            user_id=user_id,
            text=text,
            created_at=now,
            kind=kind,
        )
    finally:
        conn.close()


def recent_milestones(session_id: str, limit: int = 8) -> list[Milestone]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, session_id, user_id, text, created_at, kind "
            "FROM session_milestones "
            "WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [Milestone(**dict(r)) for r in rows]
    finally:
        conn.close()
