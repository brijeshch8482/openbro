"""Tests for audit logging."""

from openbro.utils.audit import _truncate_text, get_recent_logs, log_tool_execution


def test_truncate_text_short():
    assert _truncate_text("hello", 20) == "hello"


def test_truncate_text_long():
    assert _truncate_text("a" * 200, 50) == "a" * 50 + "..."


def test_log_tool_execution_does_not_raise():
    # Should never crash even if storage is unavailable
    log_tool_execution("test_tool", {"key": "value"}, "result", risk="safe")


def test_get_recent_logs_returns_list():
    logs = get_recent_logs(10)
    assert isinstance(logs, list)
