"""Visitor sessions and rate limits for the public web demo."""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class WebSettings:
    """Limits that keep a public page from burning the API key."""

    max_message_chars: int = 1500
    per_ip_per_hour: int = 20
    global_per_hour: int = 300
    max_turns_per_session: int = 25
    session_ttl_seconds: int = 1800
    max_sessions: int = 100
    max_concurrent_turns: int = 4
    max_steps: int = 5  # tool rounds per answer
    trust_proxy: bool = False  # read the client IP from X-Forwarded-For


class RateLimiter:
    """Sliding one-hour window, per key plus one global window."""

    WINDOW = 3600.0

    def __init__(self, per_key: int, global_limit: int, clock: Callable[[], float] = time.time):
        self.per_key = per_key
        self.global_limit = global_limit
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.WINDOW:
            q.popleft()

    def check(self, key: str) -> tuple[bool, int]:
        """(allowed, seconds_until_retry). Records the hit when allowed."""
        now = self._clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            self._trim(q, now)
            self._trim(self._all, now)
            if len(self._all) >= self.global_limit:
                return False, int(self.WINDOW - (now - self._all[0])) + 1
            if len(q) >= self.per_key:
                return False, int(self.WINDOW - (now - q[0])) + 1
            q.append(now)
            self._all.append(now)
            if len(self._hits) > 10000:  # drop idle keys
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True, 0


@dataclass
class Session:
    id: str
    agent: object
    created: float
    last_used: float
    turns: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    """session id → Session, with idle expiry and a size cap."""

    def __init__(
        self,
        factory: Callable[[str], object],
        ttl_seconds: int,
        max_sessions: int,
        clock: Callable[[], float] = time.time,
    ):
        self._factory = factory
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._sessions)

    def _expire(self, now: float) -> None:
        for sid in [s.id for s in self._sessions.values() if now - s.last_used > self._ttl]:
            del self._sessions[sid]

    def get_or_create(self, session_id: str | None) -> Session:
        now = self._clock()
        with self._lock:
            self._expire(now)
            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                session.last_used = now
                return session
            if len(self._sessions) >= self._max:
                oldest = min(self._sessions.values(), key=lambda s: s.last_used)
                del self._sessions[oldest.id]
            sid = secrets.token_urlsafe(16)
        # Build the agent outside the lock — it takes a moment.
        agent = self._factory(sid)
        session = Session(id=sid, agent=agent, created=now, last_used=now)
        with self._lock:
            self._sessions[sid] = session
        return session

    def drop(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None
