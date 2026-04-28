"""Core Agent - the brain of OpenBro."""

from rich.console import Console

from openbro.llm.base import Message
from openbro.llm.router import create_provider
from openbro.tools.registry import ToolRegistry
from openbro.utils.config import load_config

console = Console()


class Agent:
    def __init__(self):
        config = load_config()
        self.provider = create_provider()
        self.tool_registry = ToolRegistry()
        self.history: list[Message] = []

        system_prompt = config.get("agent", {}).get(
            "system_prompt",
            "Tu OpenBro hai - ek helpful AI bro. Friendly reh, user ki help kar.",
        )
        self.history.append(Message(role="system", content=system_prompt))
        self.max_history = config.get("agent", {}).get("max_history", 50)

        console.print(f"[dim]LLM: {self.provider.name()}[/dim]")

    def chat(self, user_input: str) -> str:
        self.history.append(Message(role="user", content=user_input))
        self._trim_history()

        tools = self.tool_registry.get_tools_schema() if self.provider.supports_tools() else None

        try:
            response = self.provider.chat(self.history, tools=tools)
        except Exception as e:
            return f"Error: {e}"

        # Handle tool calls
        if response.tool_calls:
            return self._handle_tool_calls(response)

        self.history.append(Message(role="assistant", content=response.content))
        return response.content

    def _handle_tool_calls(self, response) -> str:
        results = []
        for tool_call in response.tool_calls:
            func = tool_call.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", {})

            console.print(f"[dim]Running tool: {name}[/dim]")
            result = self.tool_registry.execute(name, args)
            results.append(f"[{name}]: {result}")

        # Send tool results back to LLM for final response
        tool_output = "\n".join(results)
        self.history.append(Message(role="assistant", content=f"Tool results:\n{tool_output}"))
        self.history.append(Message(role="user", content="Above tool results dekh ke user ko response de."))

        try:
            final = self.provider.chat(self.history)
            self.history.append(Message(role="assistant", content=final.content))
            return final.content
        except Exception as e:
            return f"Tool ran successfully but error in response: {e}\nRaw results:\n{tool_output}"

    def _trim_history(self):
        # Keep system message + last N messages
        if len(self.history) > self.max_history + 1:
            system = self.history[0]
            self.history = [system] + self.history[-(self.max_history):]
