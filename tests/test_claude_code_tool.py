"""Tests for the Claude Code orchestration tool."""

import json
from unittest.mock import MagicMock, patch

from openbro.tools.claude_code_tool import ClaudeCodeTool


def test_schema_shape():
    schema = ClaudeCodeTool().schema()
    assert schema["name"] == "claude_code"
    props = schema["parameters"]["properties"]
    assert "task" in props
    assert "cwd" in props
    assert "max_cost_usd" in props
    assert "task" in schema["parameters"]["required"]


def test_run_no_task():
    tool = ClaudeCodeTool()
    out = tool.run(task="")
    assert "required" in out.lower()


def test_run_no_claude_cli():
    tool = ClaudeCodeTool()
    with patch("openbro.tools.claude_code_tool.shutil.which", return_value=None):
        out = tool.run(task="do something")
    assert "not found" in out.lower()
    assert "claude-code" in out.lower() or "claude code" in out.lower()


def test_run_daily_budget_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "openbro.tools.claude_code_tool.get_storage_paths",
        lambda: {"base": tmp_path},
    )
    # Pre-fill spend file: today already at $10
    from datetime import date

    spend_file = tmp_path / "claude_spend.json"
    spend_file.write_text(json.dumps({"daily": {date.today().isoformat(): 10.5}, "total": 10.5}))

    tool = ClaudeCodeTool()
    with patch("openbro.tools.claude_code_tool.shutil.which", return_value="/fake/claude"):
        out = tool.run(task="do something")
    assert "budget hit" in out.lower()


def test_process_event_assistant_text():
    text_parts: list[str] = []
    files: set[str] = set()
    tools: list[str] = []
    bus = MagicMock()
    ev = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "I will read the file."}],
        },
    }
    ClaudeCodeTool._process_event(ev, text_parts, files, tools, bus)
    assert "I will read the file." in text_parts


def test_process_event_tool_use_records_file():
    text_parts: list[str] = []
    files: set[str] = set()
    tools: list[str] = []
    bus = MagicMock()
    ev = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "/tmp/x.py"},
                }
            ],
        },
    }
    ClaudeCodeTool._process_event(ev, text_parts, files, tools, bus)
    assert "/tmp/x.py" in files
    assert "Edit" in tools


def test_process_event_result_no_text():
    text_parts: list[str] = []
    bus = MagicMock()
    ev = {"type": "result", "subtype": "success", "result": "All done!"}
    ClaudeCodeTool._process_event(ev, text_parts, set(), [], bus)
    assert "All done!" in text_parts


def test_process_event_unknown_type_safe():
    bus = MagicMock()
    # should not raise
    ClaudeCodeTool._process_event({"type": "weirdo"}, [], set(), [], bus)


def test_record_spend_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "openbro.tools.claude_code_tool.get_storage_paths",
        lambda: {"base": tmp_path},
    )
    from openbro.tools.claude_code_tool import _record_spend, _today_spend

    today, total = _record_spend(0.42)
    assert abs(today - 0.42) < 0.001
    assert _today_spend() > 0
    today2, _ = _record_spend(0.10)
    assert abs(today2 - 0.52) < 0.001


def test_run_subprocess_parses_stream(tmp_path, monkeypatch):
    """Mock Popen to feed a fake stream-json conversation."""
    monkeypatch.setattr(
        "openbro.tools.claude_code_tool.get_storage_paths",
        lambda: {"base": tmp_path},
    )

    fake_lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}}
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": "b.py"}}
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Done!"}]},
            }
        ),
        json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.05}),
    ]
    fake_stdout = iter([line + "\n" for line in fake_lines])

    fake_proc = MagicMock()
    fake_proc.stdout = fake_stdout
    fake_proc.stderr = iter([])
    fake_proc.wait = MagicMock(return_value=0)

    with patch("openbro.tools.claude_code_tool.subprocess.Popen", return_value=fake_proc):
        tool = ClaudeCodeTool()
        out = tool._run_subprocess(["claude", "test"], str(tmp_path), 60)

    assert "Done!" in out
    assert "a.py" not in out  # Read doesn't add to files set
    assert "b.py" in out  # Write does
    assert "$0.05" in out
    assert "Read" in out
    assert "Write" in out
