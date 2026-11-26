"""Harness piece 7 — sub-agents (the task tool)."""

from unittest.mock import MagicMock, patch

from openbro.llm.base import LLMResponse
from openbro.tools.registry import ToolRegistry


def _call(cid, name, args):
    return {"id": cid, "function": {"name": name, "arguments": args}}


def _agent():
    from openbro.core.agent import Agent

    provider = MagicMock()
    provider.name.return_value = "fake"
    provider.supports_tools.return_value = True
    with patch("openbro.core.agent.create_provider", return_value=provider):
        agent = Agent(interactive=False)
    agent.playbooks_enabled = False
    return agent, provider


def _is_child_call(messages):
    return "YOU ARE A SUB-AGENT" in messages[0].content


# ─── registry view ────────────────────────────────────────────────────


def test_view_filters_and_shares_instances():
    reg = ToolRegistry()
    view = reg.view({"grep", "read"})
    assert set(view.list_tools()) == {"grep", "read"}
    assert view.get_tool("grep") is reg.get_tool("grep")
    assert {s["name"] for s in view.get_tools_schema()} == {"grep", "read"}


def test_view_rebinds_tool_search_to_itself():
    reg = ToolRegistry(config={"agent": {"tool_loading": "auto"}})
    view = reg.view(set(reg.list_tools()) - {"task"})
    ts = view.get_tool("tool_search")
    assert ts is not None and ts._registry is view
    view.execute("tool_search", {"select": "notification"})
    assert "notification" in view.loaded_names()
    assert "notification" not in reg.loaded_names()  # parent untouched


# ─── task tool ────────────────────────────────────────────────────────


def test_task_runs_child_and_returns_only_report():
    agent, provider = _agent()
    child_tools = {}

    def reply(messages, tools=None):
        if _is_child_call(messages):
            child_tools["names"] = {t["name"] for t in tools or []}
            return LLMResponse(content="Found 3 files: a.txt, b.txt, c.txt")
        if not any(m.role == "tool" for m in messages):
            return LLMResponse(
                content="",
                tool_calls=[
                    _call("t1", "task", {"prompt": "find fee files", "description": "fee files"})
                ],
            )
        return LLMResponse(content="3 fee files mile.")

    provider.chat.side_effect = reply
    assert agent.chat("fee wali files dhoondh") == "3 fee files mile."

    tool_msg = next(m for m in agent.history if m.role == "tool")
    assert "Sub-agent (explore) report" in tool_msg.content
    assert "a.txt, b.txt, c.txt" in tool_msg.content
    # the child's own messages never enter the parent history
    assert not any("YOU ARE A SUB-AGENT" in (m.content or "") for m in agent.history)
    saved = agent.memory.transcripts.load(agent.memory.session_id, agent.memory.user_id)
    assert [m.role for m in saved.messages] == ["user", "assistant", "tool", "assistant"]
    # explore = read-only tools, no task recursion
    assert {"grep", "read", "glob"} <= child_tools["names"]
    assert not {"shell", "file_ops", "edit", "task", "memory"} & child_tools["names"]


def test_general_subagent_gets_write_tools_but_not_task():
    agent, provider = _agent()
    seen = {}

    def reply(messages, tools=None):
        if _is_child_call(messages):
            seen["names"] = {t["name"] for t in tools or []}
            return LLMResponse(content="done")
        if not any(m.role == "tool" for m in messages):
            return LLMResponse(
                content="",
                tool_calls=[_call("t1", "task", {"prompt": "fix it", "agent_type": "general"})],
            )
        return LLMResponse(content="ok")

    provider.chat.side_effect = reply
    agent.chat("fix the report")
    assert {"shell", "edit", "file_ops"} <= seen["names"]
    assert "task" not in seen["names"]


def test_child_uses_parent_permission_gate():
    agent, provider = _agent()
    agent.permissions.mode = "boss"  # every call needs approval; silent channel denies
    agent.permissions._always_allow.add("task")  # the parent may delegate

    def reply(messages, tools=None):
        if _is_child_call(messages):
            if not any(m.role == "tool" for m in messages):
                return LLMResponse(content="", tool_calls=[_call("c1", "grep", {"pattern": "x"})])
            return LLMResponse(content="grep was denied")
        if not any(m.role == "tool" for m in messages):
            return LLMResponse(content="", tool_calls=[_call("t1", "task", {"prompt": "search x"})])
        return LLMResponse(content="ok")

    provider.chat.side_effect = reply
    agent.chat("search")
    tool_msg = next(m for m in agent.history if m.role == "tool")
    assert "grep was denied" in tool_msg.content


def test_child_step_cap():
    agent, provider = _agent()
    child_calls = []

    def reply(messages, tools=None):
        if _is_child_call(messages):
            child_calls.append(1)
            if tools is None:  # forced final call after the cap
                return LLMResponse(content="stopped at the cap")
            return LLMResponse(
                content="", tool_calls=[_call(f"c{len(child_calls)}", "datetime", {})]
            )
        if not any(m.role == "tool" for m in messages):
            return LLMResponse(content="", tool_calls=[_call("t1", "task", {"prompt": "loop"})])
        return LLMResponse(content="ok")

    provider.chat.side_effect = reply
    with patch(
        "openbro.utils.config.load_config", return_value={"agent": {"subagent_max_steps": 3}}
    ):
        agent.chat("go")
    assert len(child_calls) == 4  # 3 steps + 1 forced final answer
    tool_msg = next(m for m in agent.history if m.role == "tool" and "Sub-agent" in m.content)
    assert "stopped at the cap" in tool_msg.content


def test_task_needs_prompt_and_parent():
    from openbro.tools.task_tool import TaskTool

    assert "not connected" in TaskTool().run(prompt="x")
    agent, _ = _agent()
    assert "needs a 'prompt'" in agent.tool_registry.get_tool("task").run(prompt="  ")


def test_child_tokens_roll_up_to_parent():
    agent, provider = _agent()

    def reply(messages, tools=None):
        if _is_child_call(messages):
            return LLMResponse(content="r", usage={"input": 100, "output": 10})
        if not any(m.role == "tool" for m in messages):
            return LLMResponse(
                content="", tool_calls=[_call("t1", "task", {"prompt": "p"})], usage={"input": 1}
            )
        return LLMResponse(content="ok", usage={"input": 1})

    provider.chat.side_effect = reply
    agent.chat("go")
    assert agent.session_tokens_in >= 102 and agent.session_tokens_out >= 10
