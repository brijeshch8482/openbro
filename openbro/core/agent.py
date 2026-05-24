"""Core Agent - the brain of OpenBro."""

import threading
from collections.abc import Iterator

from rich.console import Console

from openbro.core.activity import get_bus
from openbro.core.permissions import PermissionGate, PermissionRequest
from openbro.llm.base import LLMResponse, Message
from openbro.llm.router import create_provider
from openbro.memory import MemoryManager
from openbro.tools.memory_tool import MemoryTool
from openbro.tools.registry import ToolRegistry
from openbro.utils.config import load_config
from openbro.utils.language import detect_language, language_instruction

console = Console()


class Agent:
    def __init__(
        self,
        memory: MemoryManager | None = None,
        interactive: bool = True,
        permission_gate: PermissionGate | None = None,
    ):
        config = load_config()
        try:
            self.provider = create_provider()
        except Exception as e:
            console.print(f"[red]LLM provider error: {e}[/red]")
            console.print("[yellow]Run 'openbro --setup' to reconfigure.[/yellow]")
            raise SystemExit(1)

        self.memory = memory or MemoryManager()
        self.interactive = interactive
        self.bus = get_bus()

        self.tool_registry = ToolRegistry(config=config)
        # Inject memory into the memory tool so it uses this agent's user/session
        mem_tool = self.tool_registry.get_tool("memory")
        if isinstance(mem_tool, MemoryTool):
            mem_tool._manager = self.memory

        # Permission gate
        if permission_gate is not None:
            self.permissions = permission_gate
        else:
            mode = config.get("safety", {}).get("permission_mode", "normal")
            channel = "cli" if interactive else "silent"
            self.permissions = PermissionGate(mode=mode, channel=channel)

        self.history: list[Message] = []

        self.base_system_prompt = config.get("agent", {}).get(
            "system_prompt",
            "Tu OpenBro hai - ek helpful AI bro. Friendly reh, user ki help kar.",
        )
        self.tool_names = ", ".join(self.tool_registry.list_tools())
        self.history.append(Message(role="system", content=self._build_system_prompt(None)))
        self.max_history = config.get("agent", {}).get("max_history", 50)

        self.last_language = "hinglish"
        self._lock = threading.RLock()  # serialize chat() across threads (REPL + voice)

        console.print(f"[dim]LLM: {self.provider.name()}[/dim]")
        self.bus.emit("system", f"agent ready: {self.provider.name()}")

    def _build_system_prompt(self, lang: str | None) -> str:
        memory_context = self.memory.context_prompt()
        parts = [
            self.base_system_prompt,
            (
                f"\nTere paas ye tools available hai: {self.tool_names}. "
                "Zaroorat padne pe inhe use kar."
            ),
        ]
        if memory_context:
            parts.append("\n" + memory_context)
        if lang:
            parts.append("\n" + language_instruction(lang))
        return "\n".join(parts)

    def _refresh_system_prompt(self, lang: str) -> None:
        self.history[0] = Message(role="system", content=self._build_system_prompt(lang))

    def chat(self, user_input: str) -> str:
        with self._lock:
            return self._chat_impl(user_input)

    # Max LLM round-trips per user message. Real Claude Code loops 5-20+
    # times for non-trivial requests. Cap protects against runaway loops
    # but is loose enough that legit multi-step work completes.
    MAX_TOOL_ITERATIONS = 10

    def _chat_impl(self, user_input: str) -> str:
        self.last_language = detect_language(user_input)
        self._refresh_system_prompt(self.last_language)

        self.bus.emit("user", user_input, lang=self.last_language)
        self.history.append(Message(role="user", content=user_input))
        self.memory.add("user", user_input)
        self._trim_history()

        tools = self.tool_registry.get_tools_schema() if self.provider.supports_tools() else None
        self.bus.emit("thinking", "agent thinking…")

        # ─── Agent loop (was single-shot, which forced LLM to hallucinate
        # answers after one tool returned nothing). Loop till LLM stops
        # calling tools and emits a final text response — same shape as
        # Claude Code / OpenAI Assistants API ReAct loop.
        for _iteration in range(self.MAX_TOOL_ITERATIONS):
            try:
                response = self.provider.chat(self.history, tools=tools)
            except ConnectionError:
                return (
                    "Bro, LLM se connect nahi ho pa raha. "
                    "Cloud provider use kar raha hai to internet check kar; "
                    "local model use kar raha hai to model file check kar "
                    "(openbro model list)."
                )
            except Exception as e:
                error_msg = str(e)
                # Strict matching — earlier 'rate' substring match was too
                # loose ('generate', 'iterate', 'reasoning' all matched and
                # the user got 'Rate limit hit' for unrelated errors).
                # Require the actual HTTP signal or an explicit rate-limit
                # phrase.
                if "401" in error_msg or "Unauthorized" in error_msg:
                    return "API key galat ya expired hai bhai. 'config set' se update kar."
                low = error_msg.lower()
                if "429" in error_msg or "rate limit" in low or "rate_limit" in low:
                    return "Rate limit hit ho gaya bro. Thoda ruk ke try kar."
                # Surface the full error — earlier we hid it behind a
                # generic 'Error: ...' which made debugging impossible.
                return f"Error ({type(e).__name__}): {e}"

            if not response.tool_calls:
                # Final answer — model decided no more tools needed.
                self.history.append(Message(role="assistant", content=response.content))
                self.memory.add("assistant", response.content)
                self.bus.emit("assistant", response.content)
                return response.content

            # Execute the tool calls, append results to history, LOOP.
            # On next iteration the LLM sees the results AND still has
            # tools= available — so it can call another tool (different
            # pattern, different approach) or finalize with text.
            self._execute_tool_batch(response)

        # Safety net — model is stuck looping. Force one final no-tools call.
        try:
            response = self.provider.chat(self.history)
        except Exception as e:
            return f"Max iterations hit, fallback failed: {e}"
        self.history.append(Message(role="assistant", content=response.content))
        self.memory.add("assistant", response.content)
        self.bus.emit("assistant", response.content)
        return response.content

    def stream_chat(self, user_input: str) -> Iterator[str]:
        """Stream response tokens for real-time output."""
        # Acquire lock for the duration of the stream.
        self._lock.acquire()
        try:
            yield from self._stream_chat_impl(user_input)
        finally:
            self._lock.release()

    def _stream_chat_impl(self, user_input: str) -> Iterator[str]:
        self.last_language = detect_language(user_input)
        self._refresh_system_prompt(self.last_language)
        self.bus.emit("user", user_input, lang=self.last_language)

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
        self.bus.emit("assistant", full_response)

    def _execute_tool_batch(self, response: LLMResponse) -> None:
        """Run every tool call in `response`, append results to history.

        Does NOT call the LLM — the outer loop in _chat_impl does that
        on the next iteration, with tools= still available. That's the
        whole point: if the model wants to try another approach (different
        glob, different tool, retry with sanitized args) after seeing the
        results, it can.
        """
        # First, record the assistant's tool-calling turn so the LLM has
        # context for what it just asked. We use a plain text summary —
        # not the strict OpenAI tool_calls schema — because Groq/local
        # providers serialize history as role/content only. The model
        # still understands 'I called X with args Y, got result Z'.
        call_summaries = []
        for tc in response.tool_calls:
            func = tc.get("function", {})
            call_summaries.append(f"  {func.get('name', '?')}({func.get('arguments', {})})")
        if response.content:
            self.history.append(Message(role="assistant", content=response.content))
        self.history.append(
            Message(role="assistant", content="Tools called:\n" + "\n".join(call_summaries))
        )

        results = []
        for tool_call in response.tool_calls:
            func = tool_call.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", {})

            risk = self.tool_registry.get_risk(name)
            self.bus.emit(
                "tool_start",
                f"{name} ({risk})",
                tool=name,
                args=args,
                risk=risk,
            )

            req = PermissionRequest(tool=name, args=args, risk=risk)
            allowed = self.permissions.request(req)
            confirmed = allowed

            if not allowed:
                msg = f"[{name}]: DENIED by user"
                results.append(msg)
                self.bus.emit("tool_end", msg, tool=name, ok=False)
                continue

            if risk == "moderate":
                console.print(f"[yellow]Tool: {name} ({risk})[/yellow] [dim]args: {args}[/dim]")
            elif risk == "safe":
                console.print(f"[dim]Tool: {name} ({risk})[/dim]")

            result = self.tool_registry.execute(name, args, confirmed=confirmed)
            results.append(f"[{name}]: {result}")
            self.bus.emit("tool_end", f"{name} done", tool=name, ok=True, preview=result[:200])

        # Tool results go back as a user message so the LLM treats them
        # as fresh input to react to. No 'now respond' instruction —
        # the model decides on the next iteration whether to call more
        # tools or finalize with text.
        self.history.append(Message(role="user", content="Tool results:\n" + "\n".join(results)))

    def _trim_history(self):
        if len(self.history) > self.max_history + 1:
            system = self.history[0]
            self.history = [system] + self.history[-(self.max_history) :]
