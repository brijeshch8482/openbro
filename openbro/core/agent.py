"""Core Agent - the brain of OpenBro."""

from collections.abc import Iterator

from rich.console import Console

from openbro.llm.base import LLMResponse, Message
from openbro.llm.router import create_provider
from openbro.memory import MemoryManager
from openbro.tools.memory_tool import MemoryTool
from openbro.tools.registry import ToolRegistry
from openbro.utils.config import load_config

console = Console()


class Agent:
    def __init__(self, memory: MemoryManager | None = None, interactive: bool = True):
        config = load_config()
        try:
            self.provider = create_provider()
        except Exception as e:
            console.print(f"[red]LLM provider error: {e}[/red]")
            console.print("[yellow]Run 'openbro --setup' to reconfigure.[/yellow]")
            raise SystemExit(1)

        self.memory = memory or MemoryManager()
        self.interactive = interactive

        self.tool_registry = ToolRegistry(config=config)
        # Inject memory into the memory tool so it uses this agent's user/session
        mem_tool = self.tool_registry.get_tool("memory")
        if isinstance(mem_tool, MemoryTool):
            mem_tool._manager = self.memory

        self.history: list[Message] = []

        system_prompt = config.get("agent", {}).get(
            "system_prompt",
            "Tu OpenBro hai - ek helpful AI bro. Friendly reh, user ki help kar.",
        )

        # Add available tools info + long-term memory context to system prompt
        tool_names = ", ".join(self.tool_registry.list_tools())
        memory_context = self.memory.context_prompt()

        prompt_parts = [
            system_prompt,
            f"\nTere paas ye tools available hai: {tool_names}. Zaroorat padne pe inhe use kar.",
        ]
        if memory_context:
            prompt_parts.append("\n" + memory_context)

        full_prompt = "\n".join(prompt_parts)
        self.history.append(Message(role="system", content=full_prompt))
        self.max_history = config.get("agent", {}).get("max_history", 50)

        console.print(f"[dim]LLM: {self.provider.name()}[/dim]")

    def chat(self, user_input: str) -> str:
        self.history.append(Message(role="user", content=user_input))
        self.memory.add("user", user_input)
        self._trim_history()

        tools = self.tool_registry.get_tools_schema() if self.provider.supports_tools() else None

        try:
            response = self.provider.chat(self.history, tools=tools)
        except ConnectionError:
            return (
                "Bro, LLM se connect nahi ho pa raha."
                " Check kar Ollama chal raha hai ya"
                " nahi (ollama serve)."
            )
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "Unauthorized" in error_msg:
                return "API key galat ya expired hai bhai. 'config set' se update kar."
            if "429" in error_msg or "rate" in error_msg.lower():
                return "Rate limit hit ho gaya bro. Thoda ruk ke try kar."
            return f"Error: {e}"

        # Handle tool calls
        if response.tool_calls:
            return self._handle_tool_calls(response)

        self.history.append(Message(role="assistant", content=response.content))
        self.memory.add("assistant", response.content)
        return response.content

    def stream_chat(self, user_input: str) -> Iterator[str]:
        """Stream response tokens for real-time output."""
        self.history.append(Message(role="user", content=user_input))
        self.memory.add("user", user_input)
        self._trim_history()

        full_response = ""
        try:
            for token in self.provider.stream(self.history):
                full_response += token
                yield token
        except Exception as e:
            yield f"\nError: {e}"
            return

        self.history.append(Message(role="assistant", content=full_response))
        self.memory.add("assistant", full_response)

    def _handle_tool_calls(self, response: LLMResponse) -> str:
        config = load_config()
        confirm_dangerous = config.get("safety", {}).get("confirm_dangerous", True)

        results = []
        for tool_call in response.tool_calls:
            func = tool_call.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", {})

            risk = self.tool_registry.get_risk(name)
            confirmed = True

            if risk == "dangerous" and confirm_dangerous:
                if self.interactive:
                    from rich.prompt import Confirm

                    console.print(f"\n[bold red]Dangerous tool requested:[/bold red] {name}")
                    console.print(f"[yellow]Args:[/yellow] {args}")
                    if not Confirm.ask("Allow this action?", default=False):
                        results.append(f"[{name}]: DENIED by user")
                        continue
                else:
                    # Non-interactive (e.g. Telegram) - block by default
                    results.append(
                        f"[{name}]: BLOCKED (dangerous tool, not allowed in this channel)"
                    )
                    continue
            elif risk == "moderate":
                console.print(f"[yellow]Tool: {name} ({risk})[/yellow] [dim]args: {args}[/dim]")
            else:
                console.print(f"[dim]Tool: {name} ({risk})[/dim]")

            result = self.tool_registry.execute(name, args, confirmed=confirmed)
            results.append(f"[{name}]: {result}")

        # Send tool results back to LLM for final response
        tool_output = "\n".join(results)
        self.history.append(Message(role="assistant", content=f"Tool results:\n{tool_output}"))
        self.history.append(
            Message(role="user", content="Above tool results dekh ke user ko response de.")
        )

        try:
            final = self.provider.chat(self.history)
            self.history.append(Message(role="assistant", content=final.content))
            self.memory.add("assistant", final.content)
            return final.content
        except Exception as e:
            return f"Tools ran but error in final response: {e}\nRaw:\n{tool_output}"

    def _trim_history(self):
        if len(self.history) > self.max_history + 1:
            system = self.history[0]
            self.history = [system] + self.history[-(self.max_history) :]
