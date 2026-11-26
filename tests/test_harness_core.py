"""Harness piece 1 — text tool protocol, stable
system prompt + per-call reminder, per-call audit risk."""

from unittest.mock import MagicMock, patch

from openbro.core.tool_protocol import (
    parse_tool_calls,
    render_tool_instructions,
    render_transcript,
)
from openbro.llm.base import LLMResponse, Message
from openbro.tools.base import BaseTool, RiskLevel

KNOWN = ["file_ops", "shell", "echo"]


# ─── parse_tool_calls ─────────────────────────────────────────────────


def test_parse_named_tag():
    text = (
        "Reading it now.\n"
        '<tool_call name="file_ops">{"action": "read", "path": "a.txt"}</tool_call>'
    )
    clean, calls = parse_tool_calls(text, KNOWN)
    assert clean == "Reading it now."
    assert calls == [
        {
            "id": "text_0",
            "function": {"name": "file_ops", "arguments": {"action": "read", "path": "a.txt"}},
        }
    ]


def test_parse_multiple_calls_keep_order():
    text = (
        '<tool_call name="shell">{"command": "dir"}</tool_call>\n'
        '<tool_call>{"name": "file_ops", "arguments": {"action": "list"}}</tool_call>\n'
        "<tool_call name='echo'>{}</tool_call>"
    )
    _, calls = parse_tool_calls(text, KNOWN)
    assert [c["function"]["name"] for c in calls] == ["shell", "file_ops", "echo"]
    assert [c["id"] for c in calls] == ["text_0", "text_1", "text_2"]
    assert calls[2]["function"]["arguments"] == {}


def test_parse_json_form_accepts_parameters_key_and_string_args():
    text = (
        '<tool_call>{"name": "shell", "parameters": {"command": "ls"}}</tool_call>'
        '<tool_call>{"name": "echo", "arguments": "{\\"x\\": 1}"}</tool_call>'
    )
    _, calls = parse_tool_calls(text, KNOWN)
    assert calls[0]["function"]["arguments"] == {"command": "ls"}
    assert calls[1]["function"]["arguments"] == {"x": 1}


def test_parse_fenced_body():
    text = '<tool_call name="shell">\n```json\n{"command": "ls"}\n```\n</tool_call>'
    _, calls = parse_tool_calls(text, KNOWN)
    assert calls[0]["function"]["arguments"] == {"command": "ls"}


def test_parse_unknown_tool_is_dropped_but_stripped():
    text = 'Done.\n<tool_call name="rm_everything">{"path": "/"}</tool_call>'
    clean, calls = parse_tool_calls(text, KNOWN)
    assert calls == []
    assert clean == "Done."


def test_parse_bad_json_is_dropped():
    _, calls = parse_tool_calls('<tool_call name="shell">{not json}</tool_call>', KNOWN)
    assert calls == []


def test_parse_plain_text_untouched():
    clean, calls = parse_tool_calls("Temp cleared (1.2 GB freed).", KNOWN)
    assert clean == "Temp cleared (1.2 GB freed)."
    assert calls == []


# ─── render_tool_instructions / render_transcript ─────────────────────


def test_tool_instructions_list_tools_and_args():
    schemas = [
        {
            "name": "app",
            "description": "Open or close apps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open", "close"],
                        "description": "What to do",
                    },
                    "app_name": {"type": "string"},
                },
                "required": ["action"],
            },
        },
        {"type": "function", "function": {"name": "datetime", "description": "Clock."}},
    ]
    out = render_tool_instructions(schemas)
    assert '<tool_call name="TOOL_NAME">' in out
    assert "### app" in out and "### datetime" in out
    assert "action (string, required, one of: open | close): What to do" in out
    assert "app_name (string)" in out


def test_transcript_writes_calls_and_results_back():
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="list files"),
        Message(
            role="assistant",
            content="Listing.",
            tool_calls=[
                {"id": "text_0", "function": {"name": "shell", "arguments": {"command": "dir"}}}
            ],
        ),
        Message(role="tool", content="a.txt\nb.txt", tool_call_id="text_0"),
        Message(role="assistant", content="2 files."),
    ]
    out = render_transcript(msgs)
    assert "## System\nsys" in out
    assert '<tool_call name="shell">{"command": "dir"}</tool_call>' in out
    assert '<tool_result name="shell">\na.txt\nb.txt\n</tool_result>' in out
    assert out.endswith("## Assistant\n2 files.")


# ─── Agent fixtures ───────────────────────────────────────────────────


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo the text back."
    risk = RiskLevel.SAFE

    def run(self, **kwargs) -> str:
        return f"echo: {kwargs.get('text', '')}"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
        }


def _make_agent(supports_tools: bool, replies: list[LLMResponse]):
    from openbro.core.agent import Agent

    provider = MagicMock()
    provider.name.return_value = "fake"
    provider.supports_tools.return_value = supports_tools
    provider.chat.side_effect = replies
    with patch("openbro.core.agent.create_provider", return_value=provider):
        agent = Agent(interactive=False)
    agent.playbooks_enabled = False
    agent.tool_registry._tools["echo"] = EchoTool()
    agent.tool_names = ", ".join(agent.tool_registry.list_tools())
    agent.refresh_system_prompt()
    return agent, provider


# ─── Text-protocol agent loop ─────────────────────────────────────────


def test_text_provider_tool_calls_run_through_the_loop():
    agent, provider = _make_agent(
        supports_tools=False,
        replies=[
            LLMResponse(content='On it.\n<tool_call name="echo">{"text": "hi"}</tool_call>'),
            LLMResponse(content="Echoed hi."),
        ],
    )
    assert "## TOOLS — how to call them" in agent.history[0].content
    assert "### echo" in agent.history[0].content

    out = agent.chat("echo hi")

    assert out == "Echoed hi."
    first_call = provider.chat.call_args_list[0]
    assert first_call.kwargs["tools"] is None
    tool_msgs = [m for m in agent.history if m.role == "tool"]
    assert tool_msgs[0].content == "echo: hi"
    assistant_call = next(m for m in agent.history if m.tool_calls)
    assert assistant_call.content == "On it."
    assert assistant_call.tool_calls[0]["function"]["name"] == "echo"


def test_native_provider_gets_schemas_and_no_protocol_block():
    agent, provider = _make_agent(supports_tools=True, replies=[LLMResponse(content="ok")])
    assert "## TOOLS — how to call them" not in agent.history[0].content
    agent.chat("hello")
    assert provider.chat.call_args.kwargs["tools"]


def test_native_provider_text_tags_are_not_executed():
    agent, _ = _make_agent(
        supports_tools=True,
        replies=[LLMResponse(content='<tool_call name="echo">{"text": "x"}</tool_call>')],
    )
    agent.chat("hello")
    assert not [m for m in agent.history if m.role == "tool"]


def test_provider_switch_rebuilds_prompt_once():
    agent, provider = _make_agent(supports_tools=True, replies=[LLMResponse(content="ok")] * 2)
    assert "## TOOLS — how to call them" not in agent.history[0].content
    provider.supports_tools.return_value = False
    agent.chat("hello")
    assert "## TOOLS — how to call them" in agent.history[0].content


# ─── Stable system prompt + reminder ──────────────────────────────────


def test_system_prompt_stable_across_turns_and_reminder_not_stored():
    agent, provider = _make_agent(
        supports_tools=True,
        replies=[LLMResponse(content="one"), LLMResponse(content="two")],
    )
    agent.memory.memory_block = lambda query="": "## MEMORY\n- city: Delhi"
    system_before = agent.history[0].content

    agent.chat("pehla sawaal kya hai bhai")
    agent.chat("second question in english")

    assert agent.history[0].content == system_before
    sent = provider.chat.call_args.args[0]
    assert sent[0].content == system_before
    assert "<system-reminder>" in sent[-1].content
    assert "city: Delhi" in sent[-1].content
    # Earlier user turn goes out without a reminder; nothing stored in history.
    earlier_user = [m for m in sent if m.role == "user"][0]
    assert "<system-reminder>" not in earlier_user.content
    assert all("<system-reminder>" not in m.content for m in agent.history)


# ─── Audit risk uses the call's args ──────────────────────────────────


class RiskyTool(BaseTool):
    name = "risky"
    description = "x"
    risk = RiskLevel.MODERATE

    def run(self, **kwargs) -> str:
        return "ran"

    def schema(self) -> dict:
        return {"name": self.name, "description": "", "parameters": {}}

    def compute_risk(self, args: dict) -> RiskLevel:
        return RiskLevel.DANGEROUS if args.get("force") else self.risk


def test_audit_logs_per_call_risk():
    from openbro.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg._tools["risky"] = RiskyTool()
    with patch("openbro.tools.registry.log_tool_execution") as log:
        reg.execute("risky", {"force": True})
    assert log.call_args.kwargs["risk"] == "dangerous"


# ─── CLI providers ────────────────────────────────────────────────────


def test_claude_provider_disables_builtin_tools_and_renders_protocol():
    from openbro.llm.claude_provider import ClaudeProvider

    proc = MagicMock()
    proc.communicate.return_value = ("final answer", "")
    proc.returncode = 0
    with (
        patch("openbro.llm.claude_provider.shutil.which", return_value="/bin/claude"),
        patch("openbro.llm.claude_provider.subprocess.Popen", return_value=proc) as popen,
    ):
        resp = ClaudeProvider().chat(
            [
                Message(role="user", content="hi"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[
                        {"id": "t0", "function": {"name": "echo", "arguments": {"text": "a"}}}
                    ],
                ),
                Message(role="tool", content="echo: a", tool_call_id="t0"),
            ]
        )
    cmd = popen.call_args.args[0]
    assert cmd[cmd.index("--tools") + 1] == ""
    sent = proc.stdin.write.call_args.args[0]
    assert '<tool_call name="echo">{"text": "a"}</tool_call>' in sent
    assert '<tool_result name="echo">' in sent
    assert resp.content == "final answer"
