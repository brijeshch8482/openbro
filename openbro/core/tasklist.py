"""TaskList — ordered, status-tracked sub-tasks for multi-step work.

When the user asks something that needs more than one step ("D drive me fee
documents dhoondh aur sab open kar"), the agent breaks it into a TaskList,
runs each step, and surfaces progress live. Same idea as Claude Code's
TodoWrite — gives the user a visible roadmap and lets the agent self-check
that every step actually happened.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Task:
    """One step inside a TaskList."""

    id: str
    description: str  # human-readable, what this step does
    status: TaskStatus = TaskStatus.PENDING
    # The verbatim sub-query the agent will dispatch for this step.
    # Often the same as `description` but can be more specific (a tool
    # call template, a normalized command, etc.).
    payload: str = ""
    # Free-form metadata for downstream consumers (UI, observers).
    meta: dict = field(default_factory=dict)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    result: str | None = None

    def elapsed(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at


class TaskList:
    """Thread-safe ordered task list with live status tracking.

    Used by Plan mode to track multi-step execution. Observers (typically
    the REPL renderer) subscribe via `on_change` to redraw whenever a
    status flips.
    """

    def __init__(self, title: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self._tasks: list[Task] = []
        self._lock = threading.RLock()
        self._observers: list[Any] = []
        self.created_at = time.time()

    # ─── mutation ─────────────────────────────────────────────────

    def add(self, description: str, payload: str = "", **meta: Any) -> Task:
        """Append a task, return the Task object."""
        with self._lock:
            task = Task(
                id=uuid.uuid4().hex[:8],
                description=description,
                payload=payload or description,
                meta=dict(meta),
            )
            self._tasks.append(task)
        self._notify()
        return task

    def update(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        result: str | None = None,
        error: str | None = None,
    ) -> Task | None:
        with self._lock:
            task = self._find(task_id)
            if task is None:
                return None
            if status is not None:
                if status == TaskStatus.IN_PROGRESS and task.started_at is None:
                    task.started_at = time.monotonic()
                if status in (
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.SKIPPED,
                ):
                    task.finished_at = time.monotonic()
                task.status = status
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error
        self._notify()
        return task

    def mark_in_progress(self, task_id: str) -> Task | None:
        return self.update(task_id, status=TaskStatus.IN_PROGRESS)

    def mark_completed(self, task_id: str, result: str | None = None) -> Task | None:
        return self.update(task_id, status=TaskStatus.COMPLETED, result=result)

    def mark_failed(self, task_id: str, error: str) -> Task | None:
        return self.update(task_id, status=TaskStatus.FAILED, error=error)

    def mark_skipped(self, task_id: str) -> Task | None:
        return self.update(task_id, status=TaskStatus.SKIPPED)

    def insert_after(self, after_id: str, description: str, payload: str = "") -> Task | None:
        """Insert a new task after `after_id`. Used when execution
        discovers a follow-up step that wasn't in the original plan."""
        with self._lock:
            for i, t in enumerate(self._tasks):
                if t.id == after_id:
                    new = Task(
                        id=uuid.uuid4().hex[:8],
                        description=description,
                        payload=payload or description,
                    )
                    self._tasks.insert(i + 1, new)
                    self._notify()
                    return new
        return None

    # ─── inspection ───────────────────────────────────────────────

    def all(self) -> list[Task]:
        with self._lock:
            return list(self._tasks)

    def pending(self) -> list[Task]:
        with self._lock:
            return [t for t in self._tasks if t.status == TaskStatus.PENDING]

    def next_pending(self) -> Task | None:
        with self._lock:
            for t in self._tasks:
                if t.status == TaskStatus.PENDING:
                    return t
        return None

    def is_done(self) -> bool:
        """True when every task has reached a terminal state."""
        with self._lock:
            return all(
                t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
                for t in self._tasks
            )

    def succeeded(self) -> bool:
        """True when every task completed successfully (no failures, no
        skipped). Empty list = vacuously true."""
        with self._lock:
            return all(
                t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in self._tasks
            ) and not any(t.status == TaskStatus.FAILED for t in self._tasks)

    def progress(self) -> tuple[int, int]:
        """Return (done_count, total_count)."""
        with self._lock:
            done = sum(
                1
                for t in self._tasks
                if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
            )
            return (done, len(self._tasks))

    def _find(self, task_id: str) -> Task | None:
        for t in self._tasks:
            if t.id == task_id:
                return t
        return None

    # ─── observers ────────────────────────────────────────────────

    def subscribe(self, callback: Any) -> Any:
        """Register a callback(tasklist) called whenever a task changes.
        Returns an unsubscribe function."""
        with self._lock:
            self._observers.append(callback)

        def _unsub() -> None:
            with self._lock:
                if callback in self._observers:
                    self._observers.remove(callback)

        return _unsub

    def _notify(self) -> None:
        with self._lock:
            observers = list(self._observers)
        for obs in observers:
            try:
                obs(self)
            except Exception:
                pass

    # ─── rendering ────────────────────────────────────────────────

    def render_markdown(self) -> str:
        """Render as a markdown checklist — Rich shows it nicely.

        Used by the REPL to surface a live plan. Each line:
          - [✓] / [⏵] / [ ] description (elapsed)
        """
        if not self._tasks:
            return ""
        lines = []
        if self.title:
            lines.append(f"### {self.title}")
            lines.append("")
        for i, t in enumerate(self._tasks, 1):
            mark = {
                TaskStatus.PENDING: "[ ]",
                TaskStatus.IN_PROGRESS: "[⏵]",
                TaskStatus.COMPLETED: "[✓]",
                TaskStatus.FAILED: "[✗]",
                TaskStatus.SKIPPED: "[~]",
            }[t.status]
            line = f"{i}. {mark} {t.description}"
            elapsed = t.elapsed()
            if elapsed is not None and t.status != TaskStatus.PENDING:
                line += f"  _({elapsed:.1f}s)_"
            if t.error:
                line += f"  — error: {t.error[:80]}"
            lines.append(line)
        return "\n".join(lines)
