"""Harness piece 6 — progressive disclosure (deferred tools + tool_search)."""

from unittest.mock import MagicMock, patch

from openbro.llm.base import LLMResponse
from openbro.tools.base import BaseTool, RiskLevel
from openbro.tools.registry import DEFAULT_DEFERRED, ToolRegistry


class FakeSkill(BaseTool):
    name = "weather_skill"
    description = "Get the weather forecast for a city. Uses a public API."
    risk = RiskLevel.SAFE

    def run(self, city: str = "") -> str:
        return f"sunny in {city}"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }


def _registry(mode="auto", deferred=None):
    agent_cfg = {"tool_loading": mode}
    if deferred is not None:
        agent_cfg["deferred_tools"] = deferred
    reg = ToolRegistry(config={"agent": agent_cfg})
    reg._tools["weather_skill"] = FakeSkill()  # an extension tool
    reg.configure_loading({"agent": agent_cfg})
    return reg


def _schema_names(reg):
    return {s["name"] for s in reg.get_tools_schema()}


def test_auto_defers_extensions_and_default_list():
    reg = _registry()
    extensions = set(reg.list_tools()) - reg._builtin_names - {"tool_search"}
    assert "weather_skill" in extensions
    assert reg.deferred_names() == set(DEFAULT_DEFERRED) | extensions
    names = _schema_names(reg)
    assert "weather_skill" not in names and "notification" not in names
    assert "tool_search" in names and "shell" in names
    assert "weather_skill" in reg.list_tools()  # still executable


def test_all_mode_sends_everything():
    reg = _registry(mode="all")
    assert reg.deferred_names() == set()
    assert "tool_search" not in reg.list_tools()
    assert {"weather_skill", "notification"} <= _schema_names(reg)


def test_custom_deferred_list():
    reg = _registry(deferred=["excel", "word"])
    builtins_deferred = reg.deferred_names() & reg._builtin_names
    assert builtins_deferred == {"excel", "word"}


def test_deferred_index_one_line_each():
    reg = _registry()
    lines = reg.deferred_index()
    assert any(line.startswith("- weather_skill — Get the weather forecast") for line in lines)
    assert len(lines) == len(reg.deferred_names())


def test_tool_search_select_loads_schema():
    reg = _registry()
    out = reg.execute("tool_search", {"select": "weather_skill, nope"})
    assert "Loaded weather_skill" in out and "city (string)" in out and "Unknown: nope" in out
    assert "weather_skill" in _schema_names(reg)
    assert "weather_skill" not in reg.deferred_names()


def test_tool_search_query_ranks():
    reg = _registry()
    out = reg.execute("tool_search", {"query": "weather forecast for my city"})
    assert out.startswith("Loaded weather_skill")


def test_tool_search_needs_input():
    assert "Pass select" in _registry().execute("tool_search", {})


def test_direct_call_auto_loads():
    reg = _registry()
    assert reg.execute("weather_skill", {"city": "Pune"}) == "sunny in Pune"
    assert "weather_skill" in _schema_names(reg)


def _agent(supports_tools=True):
    from openbro.core.agent import Agent

    provider = MagicMock()
    provider.name.return_value = "fake"
    provider.supports_tools.return_value = supports_tools
    with patch("openbro.core.agent.create_provider", return_value=provider):
        agent = Agent(interactive=False)
    agent.playbooks_enabled = False
    agent.tool_registry._tools["weather_skill"] = FakeSkill()
    agent.tool_registry.configure_loading({"agent": {"tool_loading": "auto"}})
    agent.tool_names = ", ".join(agent.tool_registry.active_tools())
    agent.refresh_system_prompt()
    return agent, provider


def test_agent_prompt_lists_deferred_and_loads_mid_turn():
    agent, provider = _agent()
    assert "## MORE TOOLS" in agent.history[0].content
    assert "- weather_skill — " in agent.history[0].content
    provider.chat.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": "a",
                    "function": {"name": "tool_search", "arguments": {"select": "weather_skill"}},
                }
            ],
        ),
        LLMResponse(
            content="",
            tool_calls=[
                {"id": "b", "function": {"name": "weather_skill", "arguments": {"city": "Delhi"}}}
            ],
        ),
        LLMResponse(content="Delhi me dhoop hai."),
    ]
    assert agent.chat("delhi ka mausam") == "Delhi me dhoop hai."
    first_tools = {t["name"] for t in provider.chat.call_args_list[0].kwargs["tools"]}
    second_tools = {t["name"] for t in provider.chat.call_args_list[1].kwargs["tools"]}
    assert "weather_skill" not in first_tools
    assert "weather_skill" in second_tools
    assert any(m.role == "tool" and m.content == "sunny in Delhi" for m in agent.history)


def test_text_mode_prompt_has_only_active_schemas():
    agent, _ = _agent(supports_tools=False)
    prompt = agent.history[0].content
    assert "### shell" in prompt  # active tool schema rendered
    assert "### weather_skill" not in prompt  # deferred: name only
    assert "- weather_skill — " in prompt
