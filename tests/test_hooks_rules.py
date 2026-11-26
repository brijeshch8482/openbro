"""Harness piece 8 — permission rules and hooks."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from openbro.core.hooks import Hook, HookRunner
from openbro.core.permission_rules import PermissionRules, parse_rule, rule_subject
from openbro.core.permissions import PermissionGate, PermissionRequest
from openbro.llm.base import LLMResponse

# ─── rules ────────────────────────────────────────────────────────────


def _rules(**buckets):
    return PermissionRules.from_config({"safety": {"rules": buckets}})


def test_parse_rule_forms():
    r = parse_rule("shell(git status*)")
    assert (r.tool, r.pattern) == ("shell", "git status*")
    assert parse_rule("elevate").pattern is None
    assert parse_rule("bad rule with spaces(") is None


def test_rule_subject_picks_meaningful_arg():
    assert rule_subject({"command": "git status", "timeout": 5}) == "git status"
    assert rule_subject({"path": "C:/x.txt"}) == "C:/x.txt"
    assert rule_subject({"n": 1}) == '{"n": 1}'


def test_decide_order_and_globs():
    rules = _rules(
        allow=["shell(git *)", "mcp_*"],
        ask=["shell(git push*)"],
        deny=["shell(*--force*)", "elevate"],
    )
    assert rules.decide("shell", {"command": "git status"})[0] == "allow"
    assert rules.decide("shell", {"command": "git push origin"})[0] == "ask"
    assert rules.decide("shell", {"command": "git push --force"})[0] == "deny"
    assert rules.decide("elevate", {"command": "x"})[0] == "deny"
    assert rules.decide("mcp_time_now", {})[0] == "allow"
    assert rules.decide("shell", {"command": "dir"})[0] is None


def test_rule_paths_match_either_slash():
    rules = _rules(deny=["file_ops(C:/Windows/*)"])
    assert rules.decide("file_ops", {"path": "C:\\Windows\\System32\\x.dll"})[0] == "deny"


def test_invalid_rules_are_reported():
    assert _rules(allow=["ok", "bad("]).invalid == ["bad("]


def _req(tool="shell", command="git status", risk="dangerous"):
    return PermissionRequest(tool=tool, args={"command": command}, risk=risk)


def test_gate_deny_rule_beats_auto_mode():
    gate = PermissionGate(mode="auto", channel="silent", rules=_rules(deny=["shell(*rm*)"]))
    assert gate.request(_req(command="rm -rf build")) is False
    assert gate.request(_req(command="ls")) is True


def test_gate_allow_rule_skips_prompt():
    gate = PermissionGate(mode="boss", channel="silent", rules=_rules(allow=["shell(git *)"]))
    assert gate.request(_req(command="git status")) is True  # silent would deny a prompt
    assert gate.request(_req(command="del x")) is False


def test_gate_ask_rule_prompts_even_in_auto_and_after_always():
    gate = PermissionGate(mode="auto", channel="silent", rules=_rules(ask=["shell(git push*)"]))
    gate._always_allow.add("shell")
    assert gate.request(_req(command="git push")) is False  # prompted → silent denies
    assert gate.request(_req(command="git status")) is True


# ─── hook runner ──────────────────────────────────────────────────────


def _script(tmp_path, name, body):
    path = tmp_path / f"{name}.py"
    path.write_text("import json, sys\ndata = json.load(sys.stdin)\n" + body, encoding="utf-8")
    return f'"{sys.executable}" "{path}"'


def test_hook_ok_stdout_becomes_context(tmp_path):
    cmd = _script(tmp_path, "ok", "print('tool was ' + data['tool'])\n")
    out = HookRunner({"post_tool_use": [Hook(cmd)]}).run("post_tool_use", {"tool": "grep"}, "grep")
    assert not out.blocked and out.context == "tool was grep" and not out.errors


def test_hook_exit_2_blocks_with_stderr(tmp_path):
    cmd = _script(tmp_path, "block", "sys.stderr.write('no shell today'); sys.exit(2)\n")
    out = HookRunner({"pre_tool_use": [Hook(cmd)]}).run("pre_tool_use", {"tool": "shell"}, "shell")
    assert out.blocked and out.message == "no shell today"


def test_hook_other_exit_is_error_not_block(tmp_path):
    cmd = _script(tmp_path, "fail", "sys.stderr.write('oops'); sys.exit(1)\n")
    out = HookRunner({"stop": [Hook(cmd)]}).run("stop", {})
    assert not out.blocked and "exit 1" in out.errors[0]


def test_hook_timeout(tmp_path):
    cmd = _script(tmp_path, "slow", "import time; time.sleep(5)\n")
    out = HookRunner({"stop": [Hook(cmd, timeout=0.5)]}).run("stop", {})
    assert "timed out" in out.errors[0]


def test_hook_matcher_filters_tools(tmp_path):
    cmd = _script(tmp_path, "m", "sys.exit(2)\n")
    runner = HookRunner({"pre_tool_use": [Hook(cmd, matcher="shell")]})
    assert runner.run("pre_tool_use", {}, "grep").blocked is False
    assert runner.run("pre_tool_use", {}, "shell").blocked is True


def test_from_config_accepts_strings_and_dicts():
    runner = HookRunner.from_config(
        {"hooks": {"stop": ["echo hi", {"command": "echo x", "matcher": "s*"}, {"nope": 1}]}}
    )
    assert [h.command for h in runner.hooks["stop"]] == ["echo hi", "echo x"]
    assert runner.hooks["stop"][1].matcher == "s*"


# ─── agent integration ────────────────────────────────────────────────


@pytest.fixture
def agent():
    from openbro.core.agent import Agent

    provider = MagicMock()
    provider.name.return_value = "fake"
    provider.supports_tools.return_value = True
    with patch("openbro.core.agent.create_provider", return_value=provider):
        a = Agent(interactive=False)
    a.playbooks_enabled = False
    a.permissions.mode = "normal"
    return a, provider


def _call(name, args=None, cid="c1"):
    return LLMResponse(
        content="", tool_calls=[{"id": cid, "function": {"name": name, "arguments": args or {}}}]
    )


def test_pre_tool_hook_blocks_call(agent, tmp_path):
    a, provider = agent
    a.hooks = HookRunner(
        {
            "pre_tool_use": [
                Hook(_script(tmp_path, "b", "sys.stderr.write('not now'); sys.exit(2)\n"))
            ]
        }
    )
    provider.chat.side_effect = [_call("datetime"), LLMResponse(content="ok")]
    a.chat("time?")
    tool_msg = next(m for m in a.history if m.role == "tool")
    assert "blocked by the user's hook — not now" in tool_msg.content


def test_post_tool_hook_appends_to_result(agent, tmp_path):
    a, provider = agent
    a.hooks = HookRunner({"post_tool_use": [Hook(_script(tmp_path, "p", "print('checked')\n"))]})
    provider.chat.side_effect = [_call("datetime"), LLMResponse(content="ok")]
    a.chat("time?")
    tool_msg = next(m for m in a.history if m.role == "tool")
    assert tool_msg.content.endswith("[hook] checked")


def test_prompt_hook_blocks_turn_without_model_call(agent, tmp_path):
    a, provider = agent
    a.hooks = HookRunner(
        {
            "user_prompt_submit": [
                Hook(_script(tmp_path, "u", "sys.stderr.write('no secrets'); sys.exit(2)\n"))
            ]
        }
    )
    assert a.chat("my password is 1234") == "⛔ Blocked by your hook: no secrets"
    provider.chat.assert_not_called()
    assert not any(m.role == "user" for m in a.history)


def test_prompt_hook_context_goes_in_reminder(agent, tmp_path):
    a, provider = agent
    a.hooks = HookRunner(
        {"user_prompt_submit": [Hook(_script(tmp_path, "c", "print('sprint ends Friday')\n"))]}
    )
    provider.chat.side_effect = [LLMResponse(content="ok")]
    a.chat("plan my week")
    sent = provider.chat.call_args.args[0]
    assert "sprint ends Friday" in sent[-1].content
    assert all("sprint ends Friday" not in m.content for m in a.history)


def test_stop_hook_makes_agent_continue(agent, tmp_path):
    a, provider = agent
    marker = tmp_path / "seen"
    body = (
        "import os\n"
        f"m = r'{marker}'\n"
        "if not os.path.exists(m):\n"
        "    open(m, 'w').close(); sys.stderr.write('tests still failing'); sys.exit(2)\n"
    )
    a.hooks = HookRunner({"stop": [Hook(_script(tmp_path, "s", body))]})
    provider.chat.side_effect = [LLMResponse(content="done!"), LLMResponse(content="really done")]
    assert a.chat("fix the tests") == "really done"
    assert any(
        m.role == "user" and "[hook feedback] tests still failing" in m.content for m in a.history
    )


def test_stop_hook_continue_is_capped(agent, tmp_path):
    a, provider = agent
    a.hooks = HookRunner(
        {"stop": [Hook(_script(tmp_path, "x", "sys.exit(2)\n"))]}, max_stop_continues=2
    )
    provider.chat.side_effect = [LLMResponse(content=f"try {i}") for i in range(5)]
    assert a.chat("go") == "try 2"
    assert provider.chat.call_count == 3


def test_agent_builds_rules_from_config():
    from openbro.core.agent import Agent

    provider = MagicMock()
    provider.name.return_value = "fake"
    provider.supports_tools.return_value = True
    cfg = {"safety": {"rules": {"deny": ["elevate"]}}}
    with (
        patch("openbro.core.agent.create_provider", return_value=provider),
        patch("openbro.core.agent.load_config", return_value=cfg),
    ):
        a = Agent(interactive=False)
    assert a.permissions.rules.decide("elevate", {})[0] == "deny"
