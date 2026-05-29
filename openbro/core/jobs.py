"""Background job infrastructure — run long-running work without blocking.

When the user kicks off a long operation (a deep file search, a download,
a multi-step shell pipeline), the REPL would otherwise be frozen until
the tool returns. Background jobs solve that: the tool returns a job ID
immediately, the actual work runs in a daemon thread, and the user can
keep typing while the job runs to completion. The REPL surfaces a `jobs`
command to inspect status and a `wait <id>` to block until done.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """One unit of background work."""

    id: str
    label: str  # short human description, e.g. "shell: D-drive scan"
    status: JobStatus = JobStatus.QUEUED
    started_at: float | None = None
    finished_at: float | None = None
    result: str | None = None
    error: str | None = None
    # Free-form metadata callers can stash (tool name, args summary, etc.)
    meta: dict = field(default_factory=dict)
    # Thread + cancel event are kept on the Job so the manager can join /
    # signal. Excluded from __repr__ so logging stays clean.
    _thread: threading.Thread | None = field(default=None, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def elapsed(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at

    def is_alive(self) -> bool:
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    def request_cancel(self) -> None:
        """Signal cooperative cancellation. The function passed to
        submit() should check `job._cancel.is_set()` periodically and
        bail when set. Threads can't be hard-killed in Python without
        breaking things."""
        self._cancel.set()


class JobRegistry:
    """Singleton-style registry of background jobs.

    Thread-safe over status mutations. The REPL holds one of these per
    Agent (constructed lazily on first use). Observers can subscribe to
    job state changes via the agent's ActivityBus events (`job_started`,
    `job_finished`).
    """

    _instance: JobRegistry | None = None

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._observers: list[Callable[[Job], None]] = []

    @classmethod
    def get(cls) -> JobRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ─── lifecycle ────────────────────────────────────────────────

    def submit(
        self,
        label: str,
        fn: Callable[[Job], str],
        meta: dict | None = None,
    ) -> Job:
        """Queue a function to run in a daemon thread.

        `fn(job)` runs in the worker thread. Its return value becomes
        `job.result`. Raising sets status=FAILED with the exception
        text in `job.error`. The function should check
        `job._cancel.is_set()` periodically if it wants to respect
        cancellation.
        """
        job = Job(
            id=uuid.uuid4().hex[:10],
            label=label,
            meta=dict(meta or {}),
        )
        with self._lock:
            self._jobs[job.id] = job

        def _run():
            with self._lock:
                job.status = JobStatus.RUNNING
                job.started_at = time.monotonic()
            self._notify(job)
            try:
                out = fn(job)
                with self._lock:
                    job.result = str(out) if out is not None else ""
                    job.status = JobStatus.CANCELLED if job._cancel.is_set() else JobStatus.DONE
                    job.finished_at = time.monotonic()
            except Exception as e:
                with self._lock:
                    job.error = str(e)
                    job.status = JobStatus.FAILED
                    job.finished_at = time.monotonic()
            finally:
                self._notify(job)

        t = threading.Thread(target=_run, name=f"openbro-job-{job.id}", daemon=True)
        job._thread = t
        t.start()
        return job

    def wait(self, job_id: str, timeout: float | None = None) -> Job | None:
        """Block until the job finishes (or until timeout). Returns the
        Job or None if not found. None timeout = wait forever."""
        job = self.get_job(job_id)
        if job is None or job._thread is None:
            return job
        job._thread.join(timeout=timeout)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if job is None:
            return False
        if not job.is_alive():
            return False
        job.request_cancel()
        return True

    # ─── inspection ───────────────────────────────────────────────

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_all(self, include_finished: bool = True) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if not include_finished:
            jobs = [j for j in jobs if j.is_alive()]
        return jobs

    def alive_count(self) -> int:
        return sum(1 for j in self.list_all() if j.is_alive())

    # ─── observers ────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[Job], None]) -> Callable[[], None]:
        with self._lock:
            self._observers.append(callback)

        def _unsub() -> None:
            with self._lock:
                if callback in self._observers:
                    self._observers.remove(callback)

        return _unsub

    def _notify(self, job: Job) -> None:
        with self._lock:
            observers = list(self._observers)
        for obs in observers:
            try:
                obs(job)
            except Exception:
                pass

    # ─── rendering helpers ────────────────────────────────────────

    @staticmethod
    def render_status_table(jobs: list[Job]) -> str:
        """Render a markdown table of jobs — used by the REPL `jobs`
        command. Empty list yields an empty string."""
        if not jobs:
            return ""
        lines = ["| ID | Status | Elapsed | Label |", "| --- | --- | --- | --- |"]
        for j in jobs:
            elapsed = j.elapsed()
            elapsed_str = f"{elapsed:.1f}s" if elapsed is not None else "-"
            mark = {
                JobStatus.QUEUED: "⏳",
                JobStatus.RUNNING: "⏵",
                JobStatus.DONE: "✓",
                JobStatus.FAILED: "✗",
                JobStatus.CANCELLED: "~",
            }[j.status]
            lines.append(f"| `{j.id}` | {mark} {j.status.value} | {elapsed_str} | {j.label} |")
        return "\n".join(lines)
