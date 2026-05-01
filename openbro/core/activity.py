"""Activity bus - central event stream for what the agent is doing.

Anything the agent does (think, call tool, get tool result, spawn subprocess,
ask permission) emits an Event here. UIs (live panel, log file, future web UI)
subscribe to render them.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Event:
    # kind: thinking | tool_start | tool_end | permission | claude | system | user | assistant
    kind: str
    text: str = ""
    meta: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class ActivityBus:
    """Singleton-ish event bus. Thread-safe."""

    def __init__(self, max_history: int = 500):
        self._subs: list[Callable[[Event], None]] = []
        self._history: deque[Event] = deque(maxlen=max_history)
        self._lock = threading.Lock()

    def emit(self, kind: str, text: str = "", **meta) -> Event:
        ev = Event(kind=kind, text=text, meta=meta)
        with self._lock:
            self._history.append(ev)
            subs = list(self._subs)
        for s in subs:
            try:
                s(ev)
            except Exception:
                pass
        return ev

    def subscribe(self, fn: Callable[[Event], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def _unsub():
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return _unsub

    def history(self, limit: int | None = None) -> list[Event]:
        with self._lock:
            items = list(self._history)
        return items[-limit:] if limit else items

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


_bus: ActivityBus | None = None


def get_bus() -> ActivityBus:
    global _bus
    if _bus is None:
        _bus = ActivityBus()
    return _bus
