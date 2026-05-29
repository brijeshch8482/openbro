"""Tests for the background job infrastructure."""

from __future__ import annotations

import time

from openbro.core.jobs import Job, JobRegistry, JobStatus


# Each test gets a fresh registry so jobs don't leak across cases.
def _fresh_registry():
    JobRegistry._instance = None
    return JobRegistry.get()


def test_submit_returns_job_immediately():
    reg = _fresh_registry()
    job = reg.submit("test", lambda j: "done")
    assert isinstance(job, Job)
    assert job.id
    assert job.label == "test"


def test_job_completes_with_result():
    reg = _fresh_registry()
    job = reg.submit("compute", lambda j: "42")
    reg.wait(job.id, timeout=2.0)
    j = reg.get_job(job.id)
    assert j is not None
    assert j.status == JobStatus.DONE
    assert j.result == "42"


def test_job_records_failure_as_error():
    reg = _fresh_registry()

    def boom(j):
        raise RuntimeError("kaboom")

    job = reg.submit("fail", boom)
    reg.wait(job.id, timeout=2.0)
    j = reg.get_job(job.id)
    assert j is not None
    assert j.status == JobStatus.FAILED
    assert "kaboom" in j.error


def test_cancel_sets_event_and_status():
    reg = _fresh_registry()
    finished = []

    def runner(j):
        # Cooperative cancel: spin in 50ms chunks for up to 5s,
        # bail when cancel is set.
        for _ in range(100):
            if j._cancel.is_set():
                finished.append("cancelled")
                return "stopped early"
            time.sleep(0.05)
        finished.append("ran to end")
        return "all done"

    job = reg.submit("long", runner)
    # Let the worker get started
    time.sleep(0.15)
    assert reg.cancel(job.id) is True
    reg.wait(job.id, timeout=3.0)
    j = reg.get_job(job.id)
    assert j is not None
    assert j.status == JobStatus.CANCELLED
    assert finished[0] == "cancelled"


def test_cancel_returns_false_for_finished_job():
    reg = _fresh_registry()
    job = reg.submit("quick", lambda j: "ok")
    reg.wait(job.id, timeout=1.0)
    assert reg.cancel(job.id) is False


def test_cancel_returns_false_for_unknown_job():
    reg = _fresh_registry()
    assert reg.cancel("not-a-real-id") is False


def test_list_all_filters_by_alive():
    reg = _fresh_registry()
    done_job = reg.submit("quick", lambda j: "ok")
    reg.wait(done_job.id, timeout=1.0)
    # Start a long-running one (we won't wait for it)

    def slow(j):
        time.sleep(0.5)
        return "slow"

    alive_job = reg.submit("slow", slow)
    time.sleep(0.1)
    all_jobs = reg.list_all()
    assert len(all_jobs) == 2
    alive_only = reg.list_all(include_finished=False)
    assert len(alive_only) == 1
    assert alive_only[0].id == alive_job.id


def test_alive_count_decreases_as_jobs_finish():
    reg = _fresh_registry()
    reg.submit("a", lambda j: time.sleep(0.05) or "a")
    reg.submit("b", lambda j: time.sleep(0.05) or "b")
    assert reg.alive_count() >= 1
    time.sleep(0.5)
    assert reg.alive_count() == 0


def test_render_status_table_shows_status_markers():
    reg = _fresh_registry()
    job = reg.submit("hello", lambda j: "world")
    reg.wait(job.id, timeout=1.0)
    out = reg.render_status_table(reg.list_all())
    assert "ID" in out
    assert "done" in out
    assert "hello" in out


def test_render_empty_returns_empty_string():
    reg = _fresh_registry()
    assert reg.render_status_table([]) == ""


def test_get_job_for_unknown_returns_none():
    reg = _fresh_registry()
    assert reg.get_job("no-such-job") is None


def test_subscribe_callback_fires_on_status_changes():
    reg = _fresh_registry()
    calls = []

    def observer(j):
        calls.append(j.status)

    unsub = reg.subscribe(observer)
    job = reg.submit("x", lambda j: "y")
    reg.wait(job.id, timeout=1.0)
    # Should see RUNNING then DONE
    assert any(c == JobStatus.RUNNING for c in calls)
    assert any(c == JobStatus.DONE for c in calls)
    unsub()


def test_unsubscribe_stops_callbacks():
    reg = _fresh_registry()
    calls = []
    unsub = reg.subscribe(lambda j: calls.append(j))
    unsub()
    reg.submit("x", lambda j: "y")
    time.sleep(0.1)
    assert calls == []


def test_meta_is_preserved():
    reg = _fresh_registry()
    job = reg.submit("x", lambda j: "y", meta={"tool": "shell", "args": "test"})
    reg.wait(job.id, timeout=1.0)
    j = reg.get_job(job.id)
    assert j is not None
    assert j.meta["tool"] == "shell"


def test_elapsed_tracked_during_run():
    reg = _fresh_registry()

    def slow(j):
        time.sleep(0.15)
        return "x"

    job = reg.submit("slow", slow)
    time.sleep(0.05)
    # While running, elapsed should be positive
    mid_job = reg.get_job(job.id)
    assert mid_job is not None
    e = mid_job.elapsed()
    assert e is not None
    assert e > 0
    reg.wait(job.id, timeout=2.0)
    j = reg.get_job(job.id)
    assert j is not None
    # After finish, elapsed should match started->finished window
    assert j.elapsed() is not None
    assert j.elapsed() >= 0.1


def test_shell_tool_background_mode_returns_job_id():
    """Live integration: shell tool with background=True returns
    instantly with a job id."""
    from openbro.tools.shell_tool import ShellTool

    _fresh_registry()
    out = ShellTool().run(command="echo hi", background=True)
    assert "Started background job" in out
    assert "`" in out  # job id is backtick-wrapped


def test_python_tool_background_mode_returns_job_id():
    from openbro.tools.python_tool import PythonTool

    _fresh_registry()
    out = PythonTool().run(code="print('hi')", background=True)
    assert "Started background job" in out


def test_shell_tool_foreground_unchanged():
    """Foreground path must still produce inline output (no breaking change)."""
    from openbro.tools.shell_tool import ShellTool

    out = ShellTool().run(command="echo hello-world")
    assert "hello-world" in out
