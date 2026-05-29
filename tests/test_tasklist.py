"""Tests for TaskList — multi-step status tracking."""

from __future__ import annotations

import time

from openbro.core.tasklist import Task, TaskList, TaskStatus


def test_empty_tasklist_renders_empty_string():
    tl = TaskList(title="Plan")
    assert tl.render_markdown() == ""


def test_add_and_iterate():
    tl = TaskList()
    a = tl.add("step a")
    b = tl.add("step b")
    assert [t.description for t in tl.all()] == ["step a", "step b"]
    assert a.id != b.id


def test_status_progression():
    tl = TaskList()
    t = tl.add("step")
    assert t.status == TaskStatus.PENDING
    tl.mark_in_progress(t.id)
    assert tl.all()[0].status == TaskStatus.IN_PROGRESS
    assert tl.all()[0].started_at is not None
    tl.mark_completed(t.id, result="ok")
    assert tl.all()[0].status == TaskStatus.COMPLETED
    assert tl.all()[0].finished_at is not None
    assert tl.all()[0].result == "ok"


def test_mark_failed_records_error():
    tl = TaskList()
    t = tl.add("step")
    tl.mark_failed(t.id, "network down")
    assert tl.all()[0].status == TaskStatus.FAILED
    assert "network down" in tl.all()[0].error


def test_progress_counts():
    tl = TaskList()
    a = tl.add("a")
    b = tl.add("b")
    tl.add("c")
    assert tl.progress() == (0, 3)
    tl.mark_completed(a.id)
    assert tl.progress() == (1, 3)
    tl.mark_failed(b.id, "x")
    assert tl.progress() == (2, 3)


def test_is_done_and_succeeded():
    tl = TaskList()
    a = tl.add("a")
    b = tl.add("b")
    assert not tl.is_done()
    tl.mark_completed(a.id)
    tl.mark_completed(b.id)
    assert tl.is_done()
    assert tl.succeeded()

    tl2 = TaskList()
    c = tl2.add("c")
    tl2.mark_failed(c.id, "x")
    assert tl2.is_done()
    assert not tl2.succeeded()


def test_next_pending_picks_first_in_order():
    tl = TaskList()
    a = tl.add("a")
    b = tl.add("b")
    tl.mark_completed(a.id)
    nxt = tl.next_pending()
    assert nxt is not None
    assert nxt.id == b.id


def test_insert_after():
    tl = TaskList()
    a = tl.add("a")
    tl.add("c")
    inserted = tl.insert_after(a.id, "b")
    assert inserted is not None
    descs = [t.description for t in tl.all()]
    assert descs == ["a", "b", "c"]


def test_observer_fires_on_change():
    tl = TaskList()
    calls = []

    def observer(t):
        calls.append(len(t.all()))

    unsub = tl.subscribe(observer)
    tl.add("a")
    tl.add("b")
    a_id = tl.all()[0].id
    tl.mark_in_progress(a_id)
    assert len(calls) == 3  # 2 adds + 1 update
    unsub()
    tl.add("c")
    assert len(calls) == 3  # unsubscribed


def test_render_markdown_includes_status_glyphs():
    tl = TaskList(title="Plan")
    a = tl.add("first step")
    b = tl.add("second step")
    tl.add("third step")
    tl.mark_completed(a.id)
    tl.mark_in_progress(b.id)
    out = tl.render_markdown()
    assert "Plan" in out
    assert "[✓]" in out
    assert "[⏵]" in out
    assert "[ ]" in out


def test_elapsed_is_none_before_start():
    t = Task(id="x", description="x")
    assert t.elapsed() is None


def test_elapsed_increases_after_start():
    t = Task(id="x", description="x", started_at=time.monotonic())
    time.sleep(0.05)
    assert t.elapsed() is not None
    # Use a small but non-zero floor — clock granularity can register
    # 0 on very fast systems if we sleep too briefly.
    assert t.elapsed() >= 0.01
