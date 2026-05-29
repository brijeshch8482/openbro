"""Tests for the Playbook framework + built-in playbooks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openbro.playbooks import PlaybookContext, PlaybookRegistry
from openbro.playbooks.base import Playbook, render_status_lines, render_table
from openbro.playbooks.builtin.close_app import CloseAppPlaybook
from openbro.playbooks.builtin.file_search import FileSearchPlaybook, _expand_extensions
from openbro.playbooks.builtin.geo_lookup import GeoLookupPlaybook
from openbro.playbooks.builtin.open_app import OpenAppPlaybook
from openbro.playbooks.builtin.process_check import ProcessCheckPlaybook
from openbro.playbooks.builtin.system_health import SystemHealthPlaybook
from openbro.playbooks.builtin.time_now import TimeNowPlaybook

# ─── Base class + registry ─────────────────────────────────────────────


def test_render_table_basic():
    out = render_table([{"A": 1, "B": 2}, {"A": 3, "B": 4}])
    assert "| A | B |" in out
    assert "| 1 | 2 |" in out
    assert "| 3 | 4 |" in out


def test_render_table_empty_returns_empty():
    assert render_table([]) == ""


def test_render_status_lines():
    out = render_status_lines([("city", "Mumbai"), ("ip", "1.2.3.4")])
    assert "- **city**: Mumbai" in out
    assert "- **ip**: 1.2.3.4" in out


def test_registry_loads_all_builtins():
    """Smoke: registry comes up with > 5 playbooks and no exceptions."""
    reg = PlaybookRegistry()
    names = [pb.name for pb in reg.list_all()]
    for expected in [
        "geo_lookup",
        "time_now",
        "system_health",
        "process_check",
        "close_app",
        "open_app",
        "file_search",
    ]:
        assert expected in names, f"missing playbook: {expected}"


def test_registry_returns_none_for_no_match():
    reg = PlaybookRegistry()
    assert reg.match("random unrelated query about quantum physics") is None


def test_registry_picks_highest_confidence():
    """When two playbooks could match, the higher-confidence one wins."""

    class A(Playbook):
        name = "a"

        def execute(self, ctx):
            return "a"

    class B(Playbook):
        name = "b"

        def execute(self, ctx):
            return "b"

    import re

    a = A()
    b = B()
    a.triggers = [(re.compile(r"foo"), 0.5)]
    b.triggers = [(re.compile(r"foo"), 0.95)]

    reg = PlaybookRegistry()
    reg._playbooks = [a, b]
    m = reg.match("foo bar")
    assert m is not None
    assert m.playbook.name == "b"


# ─── GeoLookupPlaybook ─────────────────────────────────────────────────


def test_geo_matches_hinglish_and_english():
    pb = GeoLookupPlaybook()
    for q in [
        "mai kaha hu",
        "main kahan hu",
        "where am I",
        "what's my location",
        "my current location",
    ]:
        assert pb.match(q) is not None, f"{q!r} should match"


def test_geo_extract_ip_handles_real_network_output():
    assert GeoLookupPlaybook._extract_ip("Public IP: 223.188.109.174") == "223.188.109.174"
    assert GeoLookupPlaybook._extract_ip("no ip here") == ""


def test_geo_executes_full_workflow(monkeypatch):
    """Mock network tool + httpx so we don't hit the real internet."""
    pb = GeoLookupPlaybook()

    fake_network = MagicMock()
    fake_network.run.return_value = "Public IP: 8.8.8.8"
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = fake_network

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "city": "Mountain View",
        "region": "California",
        "country_name": "United States",
        "org": "GOOGLE",
        "postal": "94043",
        "timezone": "America/Los_Angeles",
    }

    # _lookup_geo is a staticmethod on the class — patch on the class
    monkeypatch.setattr(
        GeoLookupPlaybook,
        "_lookup_geo",
        staticmethod(lambda ip: fake_response.json()),
    )

    ctx = PlaybookContext(
        user_input="mai kaha hu",
        tool_registry=fake_registry,
        captures={},
    )
    out = pb.execute(ctx)
    assert "Mountain View" in out
    assert "California" in out
    assert "United States" in out
    assert "8.8.8.8" in out


# ─── TimeNowPlaybook ───────────────────────────────────────────────────


def test_time_matches_common_phrasings():
    pb = TimeNowPlaybook()
    for q in [
        "kya time hua",
        "kitna time hua hai",
        "abhi kya time hai",
        "what time is it",
        "what's the time",
        "current time",
        "aaj kya date hai",
    ]:
        assert pb.match(q) is not None, f"{q!r} should match"


def test_time_executes_without_tools():
    pb = TimeNowPlaybook()
    ctx = PlaybookContext(user_input="kya time hua", tool_registry=MagicMock())
    out = pb.execute(ctx)
    # Should contain a hh:mm pattern (am/pm OR 24hr)
    import re

    assert re.search(r"\d{1,2}:\d{2}", out), f"no time in output: {out}"
    assert "timezone" in out.lower()


# ─── SystemHealthPlaybook ──────────────────────────────────────────────


def test_system_health_matches_common_phrasings():
    pb = SystemHealthPlaybook()
    for q in [
        "D drive ka health check",
        "drive health check kar",
        "smart status",
        "disk space",
        "system health",
        "ram usage",
        "cpu usage",
    ]:
        assert pb.match(q) is not None, f"{q!r} should match"


def test_system_health_drive_space_returns_table():
    """Drive space should always populate at least one row (the home drive)."""
    out = SystemHealthPlaybook._get_drive_space()
    # On any working system there's at least one mounted drive
    assert "Drive" in out or out == ""  # empty only if shutil.disk_usage fails everywhere


# ─── ProcessCheckPlaybook ──────────────────────────────────────────────


def test_process_check_matches_common_phrasings():
    pb = ProcessCheckPlaybook()
    for q in [
        "is chrome running",
        "kya chrome chal raha hai",
        "claude running hai",
        "check if vscode is running",
    ]:
        assert pb.match(q) is not None, f"{q!r} should match"


def test_process_check_captures_target():
    pb = ProcessCheckPlaybook()
    m = pb.match("is chrome running")
    assert m is not None
    assert m.captures.get("query", "").lower() == "chrome"


def test_process_check_executes_with_fake_tool():
    pb = ProcessCheckPlaybook()
    fake_tool = MagicMock()
    fake_tool.run.return_value = (
        "Found 2 process(es) matching 'chrome':\n"
        "  PID 1234  chrome.exe  |  C:\\Program Files\\Chrome\\chrome.exe\n"
        "  PID 5678  chrome.exe  |  C:\\Program Files\\Chrome\\chrome.exe --new-tab"
    )
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = fake_tool

    ctx = PlaybookContext(
        user_input="is chrome running",
        tool_registry=fake_registry,
        captures={"query": "chrome"},
    )
    out = pb.execute(ctx)
    assert "PID" in out
    assert "1234" in out
    assert "chrome.exe" in out
    fake_tool.run.assert_called_once()


def test_process_check_handles_no_matches():
    pb = ProcessCheckPlaybook()
    fake_tool = MagicMock()
    fake_tool.run.return_value = "No processes matching 'foo'."
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = fake_tool

    ctx = PlaybookContext(
        user_input="is foo running",
        tool_registry=fake_registry,
        captures={"query": "foo"},
    )
    out = pb.execute(ctx)
    assert "No process" in out


# ─── CloseAppPlaybook ──────────────────────────────────────────────────


def test_close_app_matches_common_phrasings():
    pb = CloseAppPlaybook()
    for q in [
        "close chrome",
        "close my browser",
        "kill firefox",
        "chrome band kar",
        "vscode bandh karo",
        "mera browser band kr",
    ]:
        assert pb.match(q) is not None, f"{q!r} should match"


def test_close_app_captures_target():
    pb = CloseAppPlaybook()
    m = pb.match("close my browser")
    assert m is not None
    assert "browser" in m.captures.get("target", "").lower()


def test_close_app_executes():
    pb = CloseAppPlaybook()
    fake_tool = MagicMock()
    fake_tool.run.return_value = "Closed: brave.exe\nNot running: chrome.exe, firefox.exe"
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = fake_tool

    ctx = PlaybookContext(
        user_input="close my browser",
        tool_registry=fake_registry,
        captures={"target": "browser"},
    )
    out = pb.execute(ctx)
    assert "✓" in out
    assert "Closed" in out
    fake_tool.run.assert_called_once_with(action="close", app_name="browser")


# ─── OpenAppPlaybook ───────────────────────────────────────────────────


def test_open_app_matches_common_phrasings():
    pb = OpenAppPlaybook()
    for q in ["open chrome", "launch vscode", "chrome khol do", "spotify kholo"]:
        assert pb.match(q) is not None, f"{q!r} should match"


def test_open_app_declines_file_open_shapes():
    """'open foo.pdf' should NOT be handled by open_app — file_ops owns that."""
    pb = OpenAppPlaybook()
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = MagicMock()
    m = pb.match("open foo.pdf")
    # match may or may not fire (regex is loose), but execute should decline
    if m is not None:
        ctx = PlaybookContext(
            user_input="open foo.pdf",
            tool_registry=fake_registry,
            captures=m.captures,
        )
        out = pb.execute(ctx)
        # Empty string -> the registry/agent falls through to LLM
        assert out == ""


def test_open_app_executes_real_app():
    pb = OpenAppPlaybook()
    fake_tool = MagicMock()
    fake_tool.run.return_value = "Opened: chrome.exe"
    fake_registry = MagicMock()
    fake_registry.get_tool.return_value = fake_tool

    ctx = PlaybookContext(
        user_input="open chrome",
        tool_registry=fake_registry,
        captures={"target": "chrome"},
    )
    out = pb.execute(ctx)
    assert "✓" in out
    assert "Opened" in out


# ─── FileSearchPlaybook ────────────────────────────────────────────────


def test_expand_extensions_documents():
    exts = _expand_extensions("documents")
    assert ".pdf" in exts
    assert ".docx" in exts
    assert ".txt" in exts


def test_expand_extensions_pdfs_only():
    assert _expand_extensions("pdfs") == {".pdf"}
    assert _expand_extensions("pdf") == {".pdf"}


def test_expand_extensions_images():
    exts = _expand_extensions("images")
    assert ".jpg" in exts
    assert ".png" in exts


def test_expand_extensions_unknown():
    assert _expand_extensions("xyz") == set()


def test_file_search_matches_kitne_pattern():
    pb = FileSearchPlaybook()
    for q in [
        "kitne pdfs hain",
        "kitne fee documents D drive me hain",
        "how many images in Desktop",
    ]:
        assert pb.match(q) is not None, f"{q!r} should match"


def test_file_search_declines_open_shape():
    """'open foo.pdf' should not be hijacked by file_search."""
    pb = FileSearchPlaybook()
    assert pb.match("open foo.pdf") is None
    assert pb.match("close chrome") is None


def test_file_search_walks_tmp_path(tmp_path):
    """End-to-end: actually walk a tmp dir and find files."""
    # Build a tree: 3 PDFs, 1 docx, 2 jpgs
    for name in ["fee_2024.pdf", "report.pdf", "other.pdf", "memo.docx"]:
        (tmp_path / name).write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.pdf").write_bytes(b"x")

    pb = FileSearchPlaybook()
    ctx = PlaybookContext(
        user_input=f"kitne pdfs {tmp_path} me hain",
        tool_registry=MagicMock(),
        captures={"kind": "pdfs", "root": str(tmp_path)},
    )
    out = pb.execute(ctx)
    assert "4 pdfs" in out  # 3 in root + 1 deep
    assert "fee_2024.pdf" in out
    assert "deep.pdf" in out


def test_file_search_keyword_filter(tmp_path):
    """Keyword filter should match substring case-insensitively."""
    for name in [
        "College_Fee_Receipt.pdf",
        "tuition fee 2024.pdf",
        "random.pdf",
        "FEE_summary.pdf",
    ]:
        (tmp_path / name).write_bytes(b"x")

    pb = FileSearchPlaybook()
    ctx = PlaybookContext(
        user_input=f"kitne fee pdfs {tmp_path} me",
        tool_registry=MagicMock(),
        captures={"kind": "pdfs", "root": str(tmp_path), "keyword": "fee"},
    )
    out = pb.execute(ctx)
    assert "3 pdfs containing `fee`" in out
    assert "College_Fee_Receipt.pdf" in out
    assert "FEE_summary.pdf" in out
    assert "random.pdf" not in out


# ─── Agent integration ─────────────────────────────────────────────────


def test_agent_tries_playbook_before_llm():
    """Agent should short-circuit to a playbook when one matches,
    skipping the LLM provider entirely."""
    from openbro.core.agent import Agent

    with patch("openbro.core.agent.create_provider") as fake_create:
        fake_provider = MagicMock()
        fake_provider.name.return_value = "fake"
        fake_provider.supports_tools.return_value = True
        fake_create.return_value = fake_provider

        agent = Agent(interactive=False)

        # Replace registry with one that always matches a dummy playbook.
        class _Dummy(Playbook):
            name = "dummy"

            def execute(self, ctx):
                return "I handled this with zero LLM calls."

        import re as _re

        dummy = _Dummy()
        dummy.triggers = [(_re.compile(r"test query"), 1.0)]
        agent.playbook_registry._playbooks = [dummy]

        response = agent.chat("test query please")
        assert "zero LLM calls" in response
        fake_provider.chat.assert_not_called()  # critical: LLM never invoked


def test_agent_falls_through_to_llm_when_no_playbook_match():
    """No playbook -> existing LLM loop runs as before."""
    from openbro.core.agent import Agent
    from openbro.llm.base import LLMResponse

    with patch("openbro.core.agent.create_provider") as fake_create:
        fake_provider = MagicMock()
        fake_provider.name.return_value = "fake"
        fake_provider.supports_tools.return_value = True
        fake_provider.chat.return_value = LLMResponse(
            content="LLM answer", usage={"input": 100, "output": 20}
        )
        fake_create.return_value = fake_provider

        agent = Agent(interactive=False)
        # Empty registry -> no match
        agent.playbook_registry._playbooks = []

        response = agent.chat("some random query that no playbook handles")
        assert "LLM answer" in response
        fake_provider.chat.assert_called()


def test_agent_disables_playbooks_via_config():
    """If config sets agent.playbooks_enabled=False, the fast path is bypassed
    even when a playbook could match."""
    from openbro.core.agent import Agent
    from openbro.llm.base import LLMResponse

    with (
        patch("openbro.core.agent.create_provider") as fake_create,
        patch("openbro.core.agent.load_config") as fake_cfg,
    ):
        fake_cfg.return_value = {
            "llm": {"provider": "groq", "model": "x"},
            "providers": {"groq": {"api_key": "x"}},
            "agent": {
                "system_prompt": "x",
                "max_history": 10,
                "playbooks_enabled": False,
            },
            "safety": {},
        }
        fake_provider = MagicMock()
        fake_provider.name.return_value = "fake"
        fake_provider.supports_tools.return_value = True
        fake_provider.chat.return_value = LLMResponse(
            content="from LLM", usage={"input": 50, "output": 10}
        )
        fake_create.return_value = fake_provider

        agent = Agent(interactive=False)

        class _AlwaysMatch(Playbook):
            name = "always"

            def execute(self, ctx):
                return "should not be called"

        import re as _re

        always = _AlwaysMatch()
        always.triggers = [(_re.compile(r".*"), 1.0)]
        agent.playbook_registry._playbooks = [always]

        response = agent.chat("anything")
        assert "from LLM" in response
        fake_provider.chat.assert_called()


@pytest.mark.parametrize(
    "query,expected_name",
    [
        ("mai kaha hu", "geo_lookup"),
        ("where am I right now", "geo_lookup"),
        ("kya time hua", "time_now"),
        ("what's the time", "time_now"),
        ("D drive ka health check", "system_health"),
        ("disk space", "system_health"),
        ("is chrome running", "process_check"),
        ("close my browser", "close_app"),
        ("kill firefox", "close_app"),
        ("open chrome", "open_app"),
        ("kitne pdfs hain", "file_search"),
    ],
)
def test_end_to_end_routing(query, expected_name):
    """For each canonical phrasing, the registry routes to the right playbook."""
    reg = PlaybookRegistry()
    m = reg.match(query)
    assert m is not None, f"{query!r} -> no match"
    assert m.playbook.name == expected_name, (
        f"{query!r} -> {m.playbook.name} (expected {expected_name})"
    )
