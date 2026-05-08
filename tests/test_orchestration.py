"""Tests for CLI agent orchestration adapters + unified tool."""

import json
from unittest.mock import MagicMock, patch

from openbro.orchestration import ALL_AGENTS, available_agents, get_agent
from openbro.orchestration.aider import AiderAgent
from openbro.orchestration.base import record_spend, today_spend
from openbro.orchestration.claude import ClaudeAgent
from openbro.orchestration.codex import CodexAgent
from openbro.orchestration.gemini import GeminiAgent
from openbro.tools.cli_agent_tool import CliAgentTool

# ─── Registry ────────────────────────────────────────────────


def test_all_agents_registered():
    assert "claude" in ALL_AGENTS
    assert "codex" in ALL_AGENTS
    assert "aider" in ALL_AGENTS
    assert "gemini" in ALL_AGENTS


def test_get_agent_case_insensitive():
    assert isinstance(get_agent("CLAUDE"), ClaudeAgent)
    assert isinstance(get_agent("Codex"), CodexAgent)
    assert get_agent("nonexistent") is None


def test_available_agents_filters_by_install():
    with patch("openbro.orchestration.base.shutil.which", return_value=None):
        assert available_agents() == {}
    with patch("openbro.orchestration.base.shutil.which", return_value="/fake/bin"):
        avail = available_agents()
        assert len(avail) == 4


# ─── Spend tracking ──────────────────────────────────────────


def test_record_spend_per_agent(tmp_path, monkeypatch):
    monkeypatch.setattr("openbro.orchestration.base.get_storage_paths", lambda: {"base": tmp_path})
    today, _ = record_spend("claude", 0.30)
    assert abs(today - 0.30) < 0.001
    today2, _ = record_spend("claude", 0.20)
    assert abs(today2 - 0.50) < 0.001
    # Different agent has separate bucket
    other, _ = record_spend("codex", 0.10)
    assert abs(other - 0.10) < 0.001
    assert abs(today_spend("claude") - 0.50) < 0.001
    assert abs(today_spend("codex") - 0.10) < 0.001


# ─── Claude adapter ──────────────────────────────────────────


def test_claude_build_command_includes_max_budget():
    cmd = ClaudeAgent().build_command("do X", "/cwd", 0.5)
    assert "--max-budget-usd" in cmd
    assert "0.50" in cmd
    assert cmd[0] == "claude"
    assert "do X" in cmd


def test_claude_build_command_no_budget():
    cmd = ClaudeAgent().build_command("task", "/cwd", None)
    assert "--max-budget-usd" not in cmd


def test_claude_parse_stream():
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": "a.py"}}
                    ]
                },
            }
        ),
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done"}]}}
        ),
        json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.07}),
    ]
    events = []
    result = ClaudeAgent().parse_stream(
        iter(line + "\n" for line in lines),
        lambda kind, text="", **m: events.append((kind, text)),
    )
    assert result.success is True
    assert "Done" in result.summary
    assert "a.py" in result.files_touched
    assert "Write" in result.tools_used
    assert abs(result.cost_usd - 0.07) < 0.001


# ─── Codex adapter ───────────────────────────────────────────


def test_codex_build_command():
    cmd = CodexAgent().build_command("refactor x", "/cwd", None)
    assert cmd[0] == "codex"
    assert "exec" in cmd
    assert "refactor x" in cmd


def test_codex_parse_stream_picks_up_files():
    output = [
        "Reading src/main.py\n",
        "wrote src/util.py\n",
        "edited src/main.py with 3 changes\n",
        "Done!\n",
    ]
    events = []
    result = CodexAgent().parse_stream(
        iter(output), lambda kind, text="", **m: events.append((kind, text))
    )
    assert "src/util.py" in result.files_touched
    assert "src/main.py" in result.files_touched
    assert "Done!" in result.summary


# ─── Aider adapter ───────────────────────────────────────────


def test_aider_build_command():
    cmd = AiderAgent().build_command("add tests", "/cwd", None)
    assert cmd[0] == "aider"
    assert "--message" in cmd
    assert "--yes" in cmd


def test_aider_parse_picks_files_and_cost():
    output = [
        "Added foo.py to the chat\n",
        "Applied edit to foo.py\n",
        "Cost: $0.123 — Tokens: 5K\n",
    ]
    result = AiderAgent().parse_stream(iter(output), lambda *a, **k: None)
    assert "foo.py" in result.files_touched
    assert abs(result.cost_usd - 0.123) < 0.001


# ─── Gemini adapter ──────────────────────────────────────────


def test_gemini_build_command():
    cmd = GeminiAgent().build_command("summarise repo", "/cwd", None)
    assert cmd[0] == "gemini"
    assert "-p" in cmd
    assert "--yolo" in cmd


def test_gemini_parse_picks_files():
    output = [
        "Working on the task\n",
        "Updated README.md\n",
        "Created docs/intro.md\n",
    ]
    result = GeminiAgent().parse_stream(iter(output), lambda *a, **k: None)
    assert "README.md" in result.files_touched
    assert "docs/intro.md" in result.files_touched


# ─── Unified tool ────────────────────────────────────────────


def test_cli_agent_tool_schema():
    schema = CliAgentTool().schema()
    assert schema["name"] == "cli_agent"
    props = schema["parameters"]["properties"]
    assert "agent" in props
    assert "task" in props
    assert "agent" in schema["parameters"]["required"]
    assert "task" in schema["parameters"]["required"]


def test_cli_agent_tool_missing_args():
    out = CliAgentTool().run(agent="", task="")
    assert "required" in out.lower()


def test_cli_agent_tool_unknown_agent():
    out = CliAgentTool().run(agent="bogus", task="do X")
    assert "unknown agent" in out.lower()


def test_cli_agent_tool_not_installed():
    with patch("openbro.orchestration.base.shutil.which", return_value=None):
        out = CliAgentTool().run(agent="claude", task="do X")
    assert "not found" in out.lower()


# Tests below now also patch ensure_signed_in (added in v1 sign-in flow) so
# they exercise the actual cli_agent code path rather than the auth gate.

_FAKE_AUTH_OK = {"ready": True, "message": "test"}


def test_cli_agent_tool_budget_hit(tmp_path, monkeypatch):
    monkeypatch.setattr("openbro.orchestration.base.get_storage_paths", lambda: {"base": tmp_path})
    record_spend("claude", 11.0)  # over $10 default

    with patch("openbro.orchestration.base.shutil.which", return_value="/fake/claude"):
        with patch("openbro.orchestration.sign_in.ensure_signed_in", return_value=_FAKE_AUTH_OK):
            out = CliAgentTool().run(agent="claude", task="do X")
    assert "budget hit" in out.lower()


def test_cli_agent_full_flow(tmp_path, monkeypatch):
    """End-to-end: tool dispatches to claude adapter, parses fake stream."""
    monkeypatch.setattr("openbro.orchestration.base.get_storage_paths", lambda: {"base": tmp_path})
    fake_lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "ok"}]},
            }
        ),
        json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.02}),
    ]
    fake_proc = MagicMock()
    fake_proc.stdout = iter([line + "\n" for line in fake_lines])
    fake_proc.stderr = iter([])
    fake_proc.wait = MagicMock(return_value=0)
    fake_proc.returncode = 0

    with patch("openbro.orchestration.base.shutil.which", return_value="/fake/claude"):
        with patch("openbro.orchestration.base.subprocess.Popen", return_value=fake_proc):
            with patch(
                "openbro.orchestration.sign_in.ensure_signed_in", return_value=_FAKE_AUTH_OK
            ):
                out = CliAgentTool().run(agent="claude", task="say ok", cwd=str(tmp_path))
    assert "Claude Code finished" in out
    assert "$0.02" in out


def test_cli_agent_blocks_when_not_signed_in(tmp_path, monkeypatch):
    """The new sign-in gate: if ensure_signed_in says not ready, we return early."""
    monkeypatch.setattr("openbro.orchestration.base.get_storage_paths", lambda: {"base": tmp_path})
    not_ready = {"ready": False, "message": "Run claude /login first"}
    with patch("openbro.orchestration.base.shutil.which", return_value="/fake/claude"):
        with patch("openbro.orchestration.sign_in.ensure_signed_in", return_value=not_ready):
            out = CliAgentTool().run(agent="claude", task="do X")
    assert "sign-in" in out.lower()
    assert "claude /login" in out.lower()
