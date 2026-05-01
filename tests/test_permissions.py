"""Tests for permission gate + activity bus."""

from unittest.mock import MagicMock

from openbro.core.activity import ActivityBus, get_bus
from openbro.core.permissions import (
    PermissionGate,
    PermissionRequest,
    parse_yes_no,
)


def test_parse_yes_english():
    assert parse_yes_no("yes") is True
    assert parse_yes_no("yeah sure") is True
    assert parse_yes_no("ok do it") is True


def test_parse_yes_hindi():
    assert parse_yes_no("haan kar de") is True
    assert parse_yes_no("ha bhai") is True
    assert parse_yes_no("theek hai") is True


def test_parse_no_english():
    assert parse_yes_no("no") is False
    assert parse_yes_no("nope cancel it") is False
    assert parse_yes_no("stop") is False


def test_parse_no_hindi():
    assert parse_yes_no("nahi mat kar") is False
    assert parse_yes_no("nai chod") is False


def test_parse_unclear():
    assert parse_yes_no("") is None
    assert parse_yes_no("maybe") is None
    assert parse_yes_no("hmm") is None


def test_gate_normal_mode_safe_passes():
    gate = PermissionGate(mode="normal", channel="cli")
    req = PermissionRequest(tool="t", args={}, risk="safe")
    assert gate.needs_approval(req) is False


def test_gate_normal_mode_dangerous_asks():
    gate = PermissionGate(mode="normal", channel="cli")
    req = PermissionRequest(tool="t", args={}, risk="dangerous")
    assert gate.needs_approval(req) is True


def test_gate_boss_mode_always_asks():
    gate = PermissionGate(mode="boss", channel="cli")
    for risk in ("safe", "moderate", "dangerous"):
        req = PermissionRequest(tool="t", args={}, risk=risk)
        assert gate.needs_approval(req) is True, f"failed for {risk}"


def test_gate_auto_mode_never_asks():
    gate = PermissionGate(mode="auto", channel="cli")
    for risk in ("safe", "moderate", "dangerous"):
        req = PermissionRequest(tool="t", args={}, risk=risk)
        assert gate.needs_approval(req) is False


def test_gate_silent_channel_denies():
    gate = PermissionGate(mode="boss", channel="silent")
    req = PermissionRequest(tool="t", args={}, risk="safe")
    assert gate.request(req) is False


def test_gate_always_allow_memo():
    gate = PermissionGate(mode="boss", channel="silent")
    gate._always_allow.add("trusted_tool")
    req = PermissionRequest(tool="trusted_tool", args={}, risk="dangerous")
    assert gate.request(req) is True


def test_gate_always_deny_memo():
    gate = PermissionGate(mode="normal", channel="cli")
    gate._always_deny.add("evil_tool")
    req = PermissionRequest(tool="evil_tool", args={}, risk="dangerous")
    assert gate.request(req) is False


def test_voice_gate_yes_response():
    listener = MagicMock()
    listener.listen_once.return_value = "haan bhai kar de"
    tts = MagicMock()
    gate = PermissionGate(mode="boss", channel="voice", voice_listener=listener, tts=tts)
    req = PermissionRequest(tool="x", args={}, risk="safe")
    assert gate.request(req) is True
    tts.speak.assert_called()


def test_voice_gate_no_response():
    listener = MagicMock()
    listener.listen_once.return_value = "nahi mat kar"
    tts = MagicMock()
    gate = PermissionGate(mode="boss", channel="voice", voice_listener=listener, tts=tts)
    req = PermissionRequest(tool="x", args={}, risk="safe")
    assert gate.request(req) is False


def test_voice_gate_unclear_then_deny():
    listener = MagicMock()
    listener.listen_once.return_value = "hmm hmm hmm"
    tts = MagicMock()
    gate = PermissionGate(mode="boss", channel="voice", voice_listener=listener, tts=tts)
    req = PermissionRequest(tool="x", args={}, risk="safe")
    # 3 retries → all unclear → deny
    assert gate.request(req) is False


def test_activity_bus_emit_and_history():
    bus = ActivityBus()
    bus.emit("user", "hello")
    bus.emit("tool_start", "open chrome")
    h = bus.history()
    assert len(h) == 2
    assert h[0].kind == "user"
    assert h[1].kind == "tool_start"


def test_activity_bus_subscribe():
    bus = ActivityBus()
    captured = []
    unsub = bus.subscribe(lambda ev: captured.append(ev))
    bus.emit("system", "ready")
    bus.emit("system", "active")
    assert len(captured) == 2
    unsub()
    bus.emit("system", "after-unsub")
    assert len(captured) == 2  # no new events after unsubscribe


def test_get_bus_singleton():
    a = get_bus()
    b = get_bus()
    assert a is b
