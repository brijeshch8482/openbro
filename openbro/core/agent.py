"""Core Agent - the brain of OpenBro."""

import threading
from collections.abc import Iterator

from rich.console import Console

from openbro.core.activity import get_bus
from openbro.core.permissions import PermissionGate, PermissionRequest
from openbro.llm.base import LLMResponse, Message
from openbro.llm.router import create_provider
from openbro.memory import MemoryManager
from openbro.playbooks import PlaybookContext, PlaybookRegistry
from openbro.tools.memory_tool import MemoryTool
from openbro.tools.registry import ToolRegistry
from openbro.utils.config import load_config
from openbro.utils.language import detect_language, language_instruction

console = Console()


def _friendly_error(e: Exception) -> str:
    """User-facing error message with category + fix hint.

    The agent loop catches every exception from the LLM provider and
    formats it for chat. Generic 'Error: ...' confused users — they
    couldn't tell rate limit from auth from network from a tool-call
    schema mismatch. Each branch below picks the most actionable
    Hinglish phrasing + concrete next step.
    """
    msg = str(e)
    low = msg.lower()
    # Auth — recoverable by setting API key
    if "401" in msg or "unauthorized" in low or "invalid api key" in low:
        return (
            "❌ API key invalid hai bhai.\n"
            "   Fix: `openbro config set providers.groq.api_key gsk_YOUR_KEY`\n"
            "   Naya key: https://console.groq.com/keys"
        )
    # Rate / quota — wait or switch model
    if (
        "429" in msg
        or "rate limit" in low
        or "rate_limit" in low
        or "413" in msg
        or "tokens per minute" in low
        or "request too large" in low
    ):
        return (
            "⏱️  Rate limit hit ho gaya — saare fallback models bhi exhausted.\n"
            "   Fix: 30-60 sec ruk OR `openbro config set providers.groq.model "
            "llama-3.3-70b-versatile` (looser cap)."
        )
    # Network — likely offline
    if (
        isinstance(e, ConnectionError)
        or "connection" in low
        or "timed out" in low
        or "name resolution" in low
        or "getaddrinfo" in low
    ):
        return (
            "🌐 LLM se connect nahi ho pa raha bhai.\n"
            "   Fix: internet check kar; ya offline use kar — `openbro --offline` "
            "(local llama.cpp model chahiye, `openbro model download llama3.1:8b`)."
        )
    # Tool call schema mismatch — model generated bad args
    if "tool call validation failed" in low or "failed to parse tool call" in low:
        return (
            "🔧 Model ne tool ko galat call kiya (schema mismatch).\n"
            "   Try same query phir se — agent ka fallback chain dusra model try karega.\n"
            f"   Raw: {msg[:200]}"
        )
    # Catch-all — show type + message so it's debuggable
    return f"❌ Error ({type(e).__name__}): {msg[:400]}"


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

        # Playbooks — pre-built workflows that bypass the LLM for common
        # intents (geo lookup, close app, file search, etc.). Match on
        # intent BEFORE the LLM loop and short-circuit if confident.
        # Falls through to the LLM cleanly when no playbook matches.
        self.playbook_registry = PlaybookRegistry()
        # Allow users to disable the fast-path via config without yanking
        # the import (useful when a playbook regresses).
        self.playbooks_enabled = bool(config.get("agent", {}).get("playbooks_enabled", True))

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
        # Cumulative since process start — shown in REPL status bar so the
        # user knows their free-tier burn rate (Groq has TPM caps).
        self.session_tokens_in = 0
        self.session_tokens_out = 0
        self._turn_tokens_in = 0
        self._turn_tokens_out = 0

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
            self._world_facts_block(),
        ]
        if memory_context:
            parts.append("\n" + memory_context)
        if lang:
            parts.append("\n" + language_instruction(lang))
        return "\n".join(p for p in parts if p)

    def _world_facts_block(self) -> str:
        """User-environment facts the LLM needs every turn (e.g. OneDrive paths).

        The python tool runs subprocess so it can't use openbro's OneDrive
        path resolver — the LLM has to know the real Desktop/Documents/
        Pictures locations and include them in the snippet directly.
        Without this, `Path('~/Desktop').expanduser()` lands on the
        empty system Desktop and the user sees '0 files' for a folder
        that has 5 (real user incident).
        """
        try:
            from openbro.brain.world import detect_paths
        except Exception:
            return ""
        try:
            paths = detect_paths()
        except Exception:
            return ""
        if not paths:
            return ""
        lines = ["\n## USER ENVIRONMENT (Windows OneDrive-aware paths):"]
        # Surface every known user folder by name so the LLM can pick
        # the right one — most relevant: desktop/documents/pictures.
        for key in ("desktop", "documents", "downloads", "pictures", "videos"):
            if key in paths:
                lines.append(f"- {key}: {paths[key]}")
        if "onedrive" in paths:
            lines.append(f"- onedrive_root: {paths['onedrive']}")
        lines.append(
            "When user says 'desktop' / 'documents' / etc., USE THE PATHS ABOVE "
            "(not '~/Desktop' which may resolve to an empty system folder)."
        )
        return "\n".join(lines)

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
        import time as _time

        self.last_language = detect_language(user_input)
        self._refresh_system_prompt(self.last_language)

        self.bus.emit("user", user_input, lang=self.last_language)
        self.history.append(Message(role="user", content=user_input))
        self.memory.add("user", user_input)
        self._trim_history()

        tools = self.tool_registry.get_tools_schema() if self.provider.supports_tools() else None
        turn_started = _time.monotonic()
        # Per-turn counters so the UI can render "step N, X tokens, Ys"
        # without the agent having to thread them through every emit.
        self._turn_tokens_in = 0
        self._turn_tokens_out = 0

        # ─── Playbook fast path ──────────────────────────────────────
        # Try matching the query to a pre-built playbook. If we get a
        # confident match, execute it and skip the LLM loop entirely.
        # Zero tokens, instant response, no hallucination risk.
        if self.playbooks_enabled:
            pb_response = self._try_playbook(user_input, turn_started)
            if pb_response is not None:
                return pb_response

        self.bus.emit("thinking", "agent thinking…")

        # ─── Agent loop (was single-shot, which forced LLM to hallucinate
        # answers after one tool returned nothing). Loop till LLM stops
        # calling tools and emits a final text response — same shape as
        # Claude Code / OpenAI Assistants API ReAct loop.
        for iteration in range(self.MAX_TOOL_ITERATIONS):
            self.bus.emit(
                "llm_start",
                "calling LLM",
                step=iteration + 1,
                max_steps=self.MAX_TOOL_ITERATIONS,
            )
            llm_t0 = _time.monotonic()
            try:
                response = self.provider.chat(self.history, tools=tools)
            except Exception as e:
                return _friendly_error(e)

            # Token accounting — every provider returns usage with at least
            # {input, output}. Cumulate per turn and emit so the UI can
            # show a running counter (Claude Code parity).
            in_t = int(response.usage.get("input", 0) or 0)
            out_t = int(response.usage.get("output", 0) or 0)
            self._turn_tokens_in += in_t
            self._turn_tokens_out += out_t
            self.session_tokens_in += in_t
            self.session_tokens_out += out_t
            self.bus.emit(
                "llm_end",
                f"LLM {in_t}↓ {out_t}↑ in {_time.monotonic() - llm_t0:.1f}s",
                step=iteration + 1,
                input_tokens=in_t,
                output_tokens=out_t,
                turn_tokens_in=self._turn_tokens_in,
                turn_tokens_out=self._turn_tokens_out,
                session_tokens_in=self.session_tokens_in,
                session_tokens_out=self.session_tokens_out,
                elapsed=_time.monotonic() - llm_t0,
            )

            if not response.tool_calls:
                # Final answer — model decided no more tools needed.
                self.history.append(Message(role="assistant", content=response.content))
                self.memory.add("assistant", response.content)
                self.bus.emit(
                    "assistant",
                    response.content,
                    turn_elapsed=_time.monotonic() - turn_started,
                    turn_tokens_in=self._turn_tokens_in,
                    turn_tokens_out=self._turn_tokens_out,
                    steps=iteration + 1,
                )
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

    def _try_playbook(self, user_input: str, turn_started: float) -> str | None:
        """Run a matching playbook if confidence is high enough.

        Returns the response string when a playbook handled the query,
        or None when the agent should fall through to the LLM loop.
        Emits the same llm_start/llm_end/tool_start/tool_end events the
        UI already listens for so the live status bar shows progress —
        the only difference is `input_tokens=0, output_tokens=0` on the
        llm_end event so the status bar can show '0 tokens · playbook'.
        """
        import time as _time

        match = self.playbook_registry.match(user_input)
        if match is None:
            return None

        playbook = match.playbook
        # Surface the dispatch on the bus so the UI shows '⏵ playbook NAME'.
        # Reuse the llm_start/end shape because the live status bar already
        # listens for it — saves us a dedicated event type.
        self.bus.emit(
            "llm_start",
            f"playbook: {playbook.name}",
            step=1,
            max_steps=1,
            playbook=playbook.name,
            playbook_confidence=match.confidence,
        )
        pb_t0 = _time.monotonic()

        ctx = PlaybookContext(
            user_input=user_input,
            tool_registry=self.tool_registry,
            captures=match.captures,
            language=self.last_language,
        )
        try:
            response = playbook.execute(ctx)
        except Exception as e:
            self.bus.emit(
                "playbook_error",
                f"playbook {playbook.name} failed: {e}",
                playbook=playbook.name,
            )
            # Don't crash the turn — fall through to LLM so the user still
            # gets an answer. This preserves the 'playbooks are fast path,
            # not authoritative' guarantee.
            return None

        # Empty response from a playbook = 'I matched but decided not to
        # handle this one' (open_app does this for file-open shapes).
        # Treat as no-match and let the LLM take over.
        if not response or not response.strip():
            self.bus.emit(
                "playbook_end",
                f"playbook {playbook.name} declined",
                playbook=playbook.name,
            )
            return None

        elapsed = _time.monotonic() - pb_t0
        self.bus.emit(
            "llm_end",
            f"playbook {playbook.name} · {elapsed:.1f}s · 0 LLM tokens",
            step=1,
            input_tokens=0,
            output_tokens=0,
            turn_tokens_in=0,
            turn_tokens_out=0,
            session_tokens_in=self.session_tokens_in,
            session_tokens_out=self.session_tokens_out,
            elapsed=elapsed,
            playbook=playbook.name,
        )

        # Persist as a normal assistant turn so chat history stays consistent
        # — the LLM will see this on its next turn and won't be surprised.
        self.history.append(Message(role="assistant", content=response))
        self.memory.add("assistant", response)
        self.bus.emit(
            "assistant",
            response,
            turn_elapsed=_time.monotonic() - turn_started,
            turn_tokens_in=0,
            turn_tokens_out=0,
            steps=1,
            playbook=playbook.name,
        )
        return response

    def _execute_tool_batch(self, response: LLMResponse) -> None:
        """Run every tool call in `response`, append results to history.

        Uses proper OpenAI tool_calls + role='tool' message schema so the
        LLM sees structured round-trips. Previous version stuffed tool
        calls as plain assistant text ('Tools called: X(...)') and tool
        results as plain user text. On the next iteration the model
        echoed those lines back as its own response — real user incident:
        chat showed 'Tools called: browser({"action": "search"...})' as
        the agent's reply with no actual answer. The proper schema is
        what function-calling-tuned models are trained on.
        """
        # Assistant turn that called tools — keep the original tool_calls
        # structure; provider serializes it back into the wire format.
        self.history.append(
            Message(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
        )

        import time as _time

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

            tool_t0 = _time.monotonic()
            if not allowed:
                result = f"[{name}]: DENIED by user"
                self.bus.emit(
                    "tool_end",
                    result,
                    tool=name,
                    args=args,
                    ok=False,
                    elapsed=_time.monotonic() - tool_t0,
                    preview=result,
                )
            else:
                # No more plain `console.print("Tool: …")` here — the bus
                # subscriber in repl.py renders a richer Panel with
                # syntax-highlighted args. Removing the print stops the
                # double-render (one line + one panel for every call).
                result = self.tool_registry.execute(name, args, confirmed=confirmed)
                # Bigger preview (4000 chars) so the live panel can show
                # meaningful output, not just 200 chars. The history msg
                # stores the full result regardless.
                self.bus.emit(
                    "tool_end",
                    f"{name} done",
                    tool=name,
                    args=args,
                    ok=True,
                    preview=result[:4000],
                    full_length=len(result),
                    elapsed=_time.monotonic() - tool_t0,
                )

            # One role='tool' message per call, linked by tool_call_id.
            # This is the OpenAI/Groq Assistants spec.
            self.history.append(
                Message(
                    role="tool",
                    content=result,
                    tool_call_id=tool_call.get("id", ""),
                )
            )

    def _trim_history(self):
        if len(self.history) > self.max_history + 1:
            system = self.history[0]
            self.history = [system] + self.history[-(self.max_history) :]
