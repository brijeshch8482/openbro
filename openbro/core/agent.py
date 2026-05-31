"""Core Agent - the brain of OpenBro."""

import re
import threading
from collections.abc import Iterator

from rich.console import Console

from openbro.core import session_memory
from openbro.core.activity import get_bus
from openbro.core.permissions import PermissionGate, PermissionRequest
from openbro.core.workspace import detect_cached as detect_workspace
from openbro.llm.base import LLMResponse, Message
from openbro.llm.router import create_provider
from openbro.memory import MemoryManager
from openbro.playbooks import PlaybookContext, PlaybookRegistry
from openbro.tools.memory_tool import MemoryTool
from openbro.tools.registry import ToolRegistry
from openbro.utils.config import load_config
from openbro.utils.language import detect_language, language_instruction

console = Console()


def _detect_lazy_response_safe(text: str) -> list[str]:
    """Wrap the tech_research playbook's detector so a refactor of that
    module can't crash the agent loop. Returns [] on any import error."""
    try:
        from openbro.playbooks.builtin.tech_research import detect_lazy_response

        return detect_lazy_response(text)
    except Exception:
        return []


def _detect_fabricated_tool_call_safe(
    text: str,
    tool_calls_made: int,
    user_prompt: str | None = None,
) -> str | None:
    """Wrap the fabrication detector — never crash the agent loop.

    `user_prompt` is the latest user message — when present, the
    detector skips the multiple-code-blocks rule if the user
    explicitly asked for code/implementation/example. This avoids
    the false positive captured 2026-05-30 where 'bro full
    implementation chahiye' triggered an unnecessary escalation
    cascade ending in a context overflow on local.
    """
    try:
        from openbro.playbooks.builtin.tech_research import detect_fabricated_tool_call

        return detect_fabricated_tool_call(text, tool_calls_made, user_prompt=user_prompt)
    except Exception:
        return None


def _friendly_error(e: Exception) -> str:
    """User-facing error message with category + fix hint.

    The agent loop catches every exception from the LLM provider and
    formats it for chat. Generic 'Error: ...' confused users — they
    couldn't tell rate limit from auth from network from a tool-call
    schema mismatch. Each branch below picks the most actionable
    Hinglish phrasing + concrete next step.
    """
    # Both-providers-failed → smooth, calm message instead of raw
    # ValueError. Captured 2026-05-30: user saw 'ValueError:
    # Requested tokens (11293) exceed context window of 8192' as the
    # entire response — felt like a crash, not a degraded answer.
    try:
        from openbro.llm.fallback_provider import _FallbackChainExhausted

        if isinstance(e, _FallbackChainExhausted):
            return (
                "⏱️ Cloud aur local dono temporarily reach nahi ho "
                f"paaye. Cloud: `{e.primary}` ne `{e.primary_error[:80]}`"
                f" diya; local `{e.fallback}` ne "
                f"`{e.fallback_error[:80]}`.\n"
                "Fix options:\n"
                "  • 30-60 sec ruk ke phir try kar — cloud usually "
                "recover ho jata\n"
                "  • `/recap` se goal state dekh\n"
                "  • Local model upgrade kar (`openbro model "
                "download mistral-nemo`) — context bigger hai"
            )
    except ImportError:
        pass

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

        # Wire the fallback provider's notification callback to the
        # ActivityBus so the REPL renderer can show 'primary failed,
        # switched to local' inline.
        from openbro.llm.fallback_provider import FallbackProvider

        if isinstance(self.provider, FallbackProvider):

            def _on_fallback(primary_name: str, fallback_name: str, error: str) -> None:
                self.bus.emit(
                    "provider_fallback",
                    f"{primary_name} → {fallback_name}",
                    primary=primary_name,
                    fallback=fallback_name,
                    error=error,
                )

            self.provider.on_fallback = _on_fallback

            # Optional eager-warm of the local fallback. Loading a
            # GGUF (4-13 GB) into RAM takes 30-90s and llama-cpp's
            # initial Python prep + intermittent GIL contention can
            # make the REPL keyboard slow during boot. Real captured
            # incident 2026-05-31: even with a 5s delay the user saw
            # 'typing nahi ho rha' because the load competed with
            # prompt-toolkit's event loop.
            #
            # Default: OFF. Lazy-load on first fallback (no boot
            # cost). Opt in by setting OPENBRO_PREWARM_LOCAL=1 — power
            # users who know they'll hit local fallback can pay the
            # boot cost for a faster first switch.
            import os as _os

            if _os.environ.get("OPENBRO_PREWARM_LOCAL") == "1":
                try:
                    fb_engine = getattr(self.provider.fallback, "engine", None)
                    if fb_engine is not None and hasattr(fb_engine, "prewarm"):
                        fb_engine.prewarm()
                except Exception:
                    pass  # best-effort — chat() will load on demand if this fails

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
            self._workspace_block(),
            self._intent_check_block(),
        ]
        if memory_context:
            parts.append("\n" + memory_context)
        if lang:
            parts.append("\n" + language_instruction(lang))
        return "\n".join(p for p in parts if p)

    # Goal-setting language we auto-persist via session_memory. Kept
    # conservative — only obvious imperative + theme phrases trigger
    # so casual chat doesn't fill the table with noise.
    _GOAL_HEURISTIC = re.compile(
        r"\b(let'?s|I want to|I need to|please|"
        r"(can\s+you|could\s+you|tu)\s+(improve|fix|add|build|make|"
        r"migrate|deploy|debug|investigate|refactor|implement|setup|configure))\b",
        re.IGNORECASE,
    )

    def _maybe_record_goal(self, user_input: str) -> None:
        """Persist a goal-shaped user turn to session_memory.

        Conservative: a turn must (a) match _GOAL_HEURISTIC, (b) be
        between 10 and 200 chars (filters greetings + giant prompts),
        and (c) not be a follow-up shape ('tldr', 'more', etc.).
        Failures here never bubble — session_memory is best-effort.
        """
        text = (user_input or "").strip()
        if not (10 < len(text) < 200):
            return
        if text.lower().startswith(("tldr", "more", "again", "explain")):
            return
        if not self._GOAL_HEURISTIC.search(text):
            return
        try:
            session_memory.record_goal(
                session_id=self.memory.session_id,
                user_id=self.memory.user_id,
                text=text,
            )
        except Exception:
            pass

    def _workspace_block(self) -> str:
        """Per-turn workspace fragment — cwd, git branch, recent files.

        Cached for 60s so a long REPL session doesn't re-scan on every
        turn; the workspace doesn't change at LLM-call frequency. If
        detection fails for any reason, return empty and the prompt
        builder will skip the section.
        """
        try:
            ws = detect_workspace()
        except Exception:
            return ""
        return ws.render_prompt_block()

    def _intent_check_block(self) -> str:
        """Per-turn THINKING PRINCIPLES injected into the system prompt.

        Reframed 2026-05-31 from a narrow 'intent type check' to a
        comprehensive set of reasoning principles. Captured user
        vision: 'no hardcode...sab thinking hoga llm se...jab tak
        solve nhi hota..agent baat krega..llm se kaise solve krna
        plan bnega..everthing..plan usi time hoga'.

        The agent layer stays minimal; intelligence is the LLM's job
        guided by these principles. No regex orchestration, no
        hardcoded decompose, no fixed planner instruction. Just
        clear rules the model applies to its own reasoning.
        """
        return (
            "\n## THINKING PRINCIPLES (apply these on every turn)\n"
            "\n"
            "### 1. Plan emerges at runtime — don't follow a fixed script\n"
            "When the user's request takes more than one step, first"
            " state YOUR plan as a numbered list (1-5 concrete steps,"
            " each = one tool call or specific reasoning). Then"
            " execute step 1. After each tool result, restate what"
            " you just did and what's next. Don't pad with steps you"
            " won't actually do.\n"
            "\n"
            "### 2. Tool error → analyse + retry with a fix, never random switch\n"
            "When ANY tool returns an error / permission denied /"
            " not found / module-not-found / wrong path:\n"
            "  a. READ the error message carefully.\n"
            "  b. Diagnose: was the arg wrong? path syntax? missing"
            " dep? typo?\n"
            "  c. RETRY the SAME tool with corrected args (path"
            " variants, different action, escaped quotes, etc.).\n"
            "  d. Only after 3 failed retries with different fixes,"
            " try a different tool / approach.\n"
            "Never give up after one error. Never switch tools"
            " randomly just because the first failed.\n"
            "\n"
            "### 3. Verify before claiming success — never lie\n"
            "After any action that CLAIMS state change ('opened',"
            " 'created', 'installed', 'wrote', 'launched', 'kar"
            " diya', 'khol diya'), the NEXT step MUST be a"
            " verification:\n"
            "  • After `app open X` → call `process` to confirm X is"
            " running, OR `file_ops list` to check the .lnk/.exe"
            " was found.\n"
            "  • After `file_ops write` → call `file_ops read` to"
            " confirm contents.\n"
            "  • After `shell <install>` → call a check command"
            " (e.g. `pip show pkg`).\n"
            "Only after verification succeeds, report success to"
            " the user. Tool result said 'Opened X' is NOT proof X"
            " actually opened — Windows can return success for a"
            " non-existent app.\n"
            "\n"
            "### 4. Numeric / tabular questions → python + pandas, never reason over snippet\n"
            "When the user asks a quantitative question over a"
            " file (CSV, Excel, JSON, log):\n"
            "  • DON'T eyeball a truncated snippet and guess.\n"
            "  • DO call the `python` tool with pandas to load the"
            " full data and compute:\n"
            "        import pandas as pd\n"
            "        df = pd.read_excel(PATH)  # or read_csv / read_json\n"
            "        # Then min/max/first/last/delta/sum/mean as"
            " needed\n"
            "  • For DURATION questions: compute"
            " (last_timestamp - first_timestamp) from the time"
            " column.\n"
            "  • For RANGE: min, max, delta.\n"
            "  • For RATE: delta_value / delta_time.\n"
            "Report the actual computed number, not a guess from"
            " the head of the file.\n"
            "\n"
            "### 5. Match answer type to question type\n"
            "  • `kitna`/`how much` of ONE thing → number\n"
            "  • `kitne ghante`/`kitna time`/`how long`/`backup time`"
            " → TIME DELTA (not current state)\n"
            "  • `kab`/`when` → timestamp\n"
            "  • `kya kya`/`which`/`list` → items\n"
            "  • `kaise`/`how do I`/`steps` → ordered steps\n"
            "  • `kyun`/`why` → reasoned explanation\n"
            "If your answer's TYPE doesn't match the question's"
            " TYPE, keep calling tools until it does. Don't report"
            " a current % when asked for backup duration.\n"
            "\n"
            "### 6. Loop until solved (within the iteration cap)\n"
            "Keep calling tools until the user's task is COMPLETE,"
            " not just until the first plausible answer arrives. On"
            " local model (offline) the iteration cap is large — use"
            " it. The user explicitly asked for unbounded effort on"
            " local: 'jab tak cheeje solve na ho jaise claude'."
        )

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
        # Captured 2026-05-31: user asked to find Adobe Audition. Agent
        # only searched C:\\ and C:\\Program Files. App was at
        # D:\\softwares\\Adobe Audition. Agent gave up after the C-drive
        # searches because it didn't know D: was available. Now we
        # enumerate live drives so the model knows where to look.
        try:
            import string as _string
            from pathlib import Path as _Path

            drives = [f"{c}:\\" for c in _string.ascii_uppercase if _Path(f"{c}:\\").exists()]
            if drives:
                lines.append(f"- drives_available: {', '.join(drives)}")
                lines.append(
                    "When searching for installed apps, scan ALL listed "
                    "drives — apps may live in `C:\\Program Files`, "
                    "`C:\\Program Files (x86)`, `D:\\softwares`, etc. "
                    "Also check `C:\\Users\\Public\\Desktop\\*.lnk` for "
                    "shortcuts that resolve to outsourced installs."
                )
        except Exception:
            pass
        lines.append(
            "When user says 'desktop' / 'documents' / etc., USE THE PATHS ABOVE "
            "(not '~/Desktop' which may resolve to an empty system folder)."
        )
        return "\n".join(lines)

    def _refresh_system_prompt(self, lang: str) -> None:
        self.history[0] = Message(role="system", content=self._build_system_prompt(lang))

    def chat(self, user_input: str) -> str:
        """Single user turn — one LLM-driven loop.

        Captured 2026-05-31 user vision: 'sab thinking hoga llm se...
        plan usi time hoga'. The old code force-split compound
        queries via hardcoded regex (decompose module) and rendered
        them as a 'Compound request' TaskList. That treated the
        user's words as the plan instead of asking the LLM to
        synthesise one.

        New behaviour: ONE turn = ONE _chat_impl call. The LLM reads
        the full user message, emits its own plan (Principle #1 in
        the system prompt), and executes it via tool calls in the
        same loop. No hardcoded decomposition; the LLM decides.
        """
        with self._lock:
            return self._chat_impl(user_input)

    # Max LLM round-trips per user message. Cap protects against
    # runaway loops but is loose enough that legit multi-step work
    # completes. Two ceilings:
    #
    #   MAX_TOOL_ITERATIONS_CLOUD = 25  — cloud calls cost real tokens
    #                                     + rate limit, so a tighter
    #                                     cap is appropriate. Real
    #                                     Claude Code loops 5-20+
    #                                     times on hard tasks; 25 is
    #                                     enough headroom for that
    #                                     while still terminating on
    #                                     genuine loops.
    #
    #   MAX_TOOL_ITERATIONS_LOCAL = 80  — captured 2026-05-31 user
    #                                     ask: 'unlimited tokens lene
    #                                     do offline se...jab tak
    #                                     cheeje solve na ho jaise
    #                                     claude krta'. Local runs in-
    #                                     process so no rate limit;
    #                                     let it grind through. Hard
    #                                     ceiling of 80 keeps a real
    #                                     infinite loop from running
    #                                     forever; user can Ctrl+C
    #                                     anytime.
    #
    # `_iteration_cap()` picks the right one based on the active
    # provider type.
    MAX_TOOL_ITERATIONS_CLOUD = 25
    MAX_TOOL_ITERATIONS_LOCAL = 80
    # Kept for back-compat — older tests / external integrations
    # read this attribute. Defaults to the cloud cap.
    MAX_TOOL_ITERATIONS = 25

    def _iteration_cap(self) -> int:
        """Return the per-turn iteration ceiling for the active provider.

        Local llama.cpp gets the higher cap (offline = no rate limit,
        let it keep trying). Cloud (Groq / Anthropic / OpenAI) gets
        the lower cap to protect token budget.
        """
        from openbro.llm.fallback_provider import FallbackProvider

        provider = self.provider
        if isinstance(provider, FallbackProvider):
            provider = getattr(provider, "primary", provider)
        name = (provider.name() if hasattr(provider, "name") else "").lower()
        if name.startswith("local") or "llama_cpp" in name or "llamacpp" in name:
            return self.MAX_TOOL_ITERATIONS_LOCAL
        return self.MAX_TOOL_ITERATIONS_CLOUD

    def _chat_impl(self, user_input: str) -> str:
        # Snapshot the model + provider type so we can restore at the
        # end of the turn. Captured 2026-05-30: escalator round 3
        # swapped Groq from llama-3.3 to llama-4-maverick — the swap
        # persisted across turns because we never restored. Every
        # subsequent turn ran on maverick → maverick unavailable →
        # fallback to local → context overflow. Snapshot here, restore
        # in the finally so the next turn always starts from the
        # user-configured model.
        original_provider = self.provider
        original_model = getattr(self.provider, "model", None)
        try:
            return self._chat_impl_inner(user_input)
        finally:
            if self.provider is not original_provider:
                self.provider = original_provider
                self.bus.emit("system", "turn end: restored original provider")
            elif (
                original_model is not None
                and getattr(self.provider, "model", None) != original_model
            ):
                try:
                    self.provider.model = original_model
                    self.bus.emit("system", f"turn end: restored model to {original_model}")
                except Exception:
                    pass

    def _chat_impl_inner(self, user_input: str) -> str:
        import time as _time

        from openbro.core.reflection_escalator import ReflectionEscalator

        self.last_language = detect_language(user_input)
        self._refresh_system_prompt(self.last_language)

        self.bus.emit("user", user_input, lang=self.last_language)
        self.history.append(Message(role="user", content=user_input))
        self.memory.add("user", user_input)
        # Auto-record goals from goal-setting user turns. Persists to
        # SQLite so `openbro --resume` next session can pick up the
        # same goal. De-duplicates by text — same goal repeated isn't
        # re-recorded.
        self._maybe_record_goal(user_input)
        self._trim_history()

        tools = self.tool_registry.get_tools_schema() if self.provider.supports_tools() else None
        turn_started = _time.monotonic()
        # Per-turn counters so the UI can render "step N, X tokens, Ys"
        # without the agent having to thread them through every emit.
        self._turn_tokens_in = 0
        self._turn_tokens_out = 0

        # Per-turn escalator: when the LLM produces a fabricated /
        # lazy response, advance to the next strategy (harder prompt,
        # model swap, local fallback, simplify, honest stop). Replaces
        # the old 1-retry cap. See reflection_escalator.py for the
        # default chain of 6 rounds. Fresh instance per turn so the
        # next turn starts clean.
        escalator = ReflectionEscalator()

        # ─── Playbook fast path ──────────────────────────────────────
        # Try matching the query to a pre-built playbook. If we get a
        # confident match, execute it and skip the LLM loop entirely.
        # Zero tokens, instant response, no hallucination risk.
        if self.playbooks_enabled:
            pb_response = self._try_playbook(user_input, turn_started)
            if pb_response is not None:
                return pb_response

        self.bus.emit("thinking", "agent thinking…")

        # Track how many tool calls were dispatched this turn — used
        # by the reflection layer to detect "wrote code in chat instead
        # of calling python tool" (captured failure: zero tool calls
        # made AND response had fake Output: blocks).
        turn_tool_calls_made = 0

        # ─── Agent loop (was single-shot, which forced LLM to hallucinate
        # answers after one tool returned nothing). Loop till LLM stops
        # calling tools and emits a final text response — same shape as
        # Claude Code / OpenAI Assistants API ReAct loop.
        max_iterations = self._iteration_cap()
        for iteration in range(max_iterations):
            self.bus.emit(
                "llm_start",
                "calling LLM",
                step=iteration + 1,
                max_steps=max_iterations,
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
                # ─── Reflection: escalating strategy chain ────────────
                # When the LLM produces a fabricated/lazy response, the
                # ReflectionEscalator advances to the next strategy:
                # harder prompt → model swap → local fallback →
                # simplify context → honest stop. Each retry tries
                # something DIFFERENT — unbounded same-retry is
                # useless on a weak model (always same fabrication).
                # See reflection_escalator.py for the chain.
                lazy_markers = _detect_lazy_response_safe(response.content)
                fabricated_reason = _detect_fabricated_tool_call_safe(
                    response.content,
                    turn_tool_calls_made,
                    user_prompt=user_input,
                )
                needs_retry = bool(fabricated_reason or lazy_markers)
                if needs_retry:
                    trigger = fabricated_reason or (f"lazy markers: {', '.join(lazy_markers[:3])}")
                    strategy = escalator.next_strategy(trigger=trigger)
                    # Either chain exhausted OR landed on honest_stop —
                    # surface the failure transparently instead of
                    # showing the user another fabricated answer.
                    if strategy is None or strategy.is_honest_stop:
                        self.bus.emit(
                            "fabrication_persisted",
                            f"escalator exhausted after {escalator.rounds_used()} rounds",
                            tried=escalator.history,
                            last_trigger=trigger,
                        )
                        honest = escalator.build_honest_stop_message(last_trigger=trigger)
                        self.history.append(Message(role="assistant", content=honest))
                        self.memory.add("assistant", honest)
                        self.history = [
                            m
                            for m in self.history
                            if not (
                                m.role == "system"
                                and (
                                    "[TRANSIENT_RESEARCH]" in (m.content or "")
                                    or "[TRANSIENT_PLAN]" in (m.content or "")
                                )
                            )
                        ]
                        self.bus.emit(
                            "assistant",
                            honest,
                            turn_elapsed=_time.monotonic() - turn_started,
                            turn_tokens_in=self._turn_tokens_in,
                            turn_tokens_out=self._turn_tokens_out,
                            steps=iteration + 1,
                        )
                        return honest
                    # Apply the strategy: emit event, optionally swap
                    # model, optionally simplify, then inject prompt
                    # and loop.
                    self.bus.emit(
                        "escalation_round",
                        f"Round {escalator.rounds_used() + 1}/6 — {strategy.description}",
                        round=escalator.rounds_used(),
                        strategy=strategy.name,
                        description=strategy.description,
                        trigger=trigger,
                    )
                    if strategy.model_swap:
                        try:
                            self._swap_model_for_retry(strategy.model_swap)
                        except Exception as e:  # pragma: no cover — defensive
                            self.bus.emit(
                                "system",
                                f"model swap failed ({strategy.model_swap}): {e}",
                            )
                    if strategy.simplify:
                        # Drop transient context blocks so the retry
                        # sees only the original user question + any
                        # real tool results.
                        self.history = [
                            m
                            for m in self.history
                            if not (
                                m.role == "system"
                                and (
                                    "[TRANSIENT_RESEARCH]" in (m.content or "")
                                    or "[TRANSIENT_PLAN]" in (m.content or "")
                                )
                            )
                        ]
                    if strategy.prompt_injection:
                        self.history.append(
                            Message(role="system", content=strategy.prompt_injection)
                        )
                    continue  # re-run the LLM with the new strategy

                # Final answer — model decided no more tools needed.
                self.history.append(Message(role="assistant", content=response.content))
                self.memory.add("assistant", response.content)
                # Prune any [TRANSIENT_RESEARCH] / [TRANSIENT_PLAN]
                # system messages now that the synthesis is done. They were one-turn context for
                # the LLM; keeping them across turns bloats every future
                # request (captured: 12K-token retries / 413 cascades /
                # local context overflow). The assistant's final answer
                # IS persisted as a normal turn so the conversation flow
                # is unaffected.
                self.history = [
                    m
                    for m in self.history
                    if not (
                        m.role == "system"
                        and (
                            "[TRANSIENT_RESEARCH]" in (m.content or "")
                            or "[TRANSIENT_PLAN]" in (m.content or "")
                        )
                    )
                ]
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
            turn_tool_calls_made += len(response.tool_calls)
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

    def _trim_history_for_local_swap(self, target_token_budget: int) -> None:
        """Aggressively trim history when escalating to a local model.

        Local llama.cpp models default to 8K context vs Groq's 32K.
        Captured failure 2026-05-30: cloud-only history of 13K tokens
        was sent unchanged to local llama3.2:3b → ValueError
        'requested (13658) exceed context window (8192)'. The user
        saw an error instead of an answer.

        Strategy:
          1. Drop every [TRANSIENT_RESEARCH] / [TRANSIENT_PLAN]
             system message (research bloats history with 15K+ char
             source dumps).
          2. Keep the original system prompt (index 0).
          3. Walk the rest tail-first, keeping messages until the
             estimated token count exceeds the budget.
          4. Token estimate: 4 chars ≈ 1 token (good enough — better
             to under-trim than blow the context).

        Best-effort: if estimation fails for any reason, fall back to
        keeping the system prompt + last 6 turns.
        """
        budget = max(1024, int(target_token_budget))
        # Step 1: prune transient context blocks.
        history = [
            m
            for m in self.history
            if not (
                m.role == "system"
                and (
                    "[TRANSIENT_RESEARCH]" in (m.content or "")
                    or "[TRANSIENT_PLAN]" in (m.content or "")
                )
            )
        ]
        if not history:
            return

        def _approx_tokens(msg) -> int:
            return max(1, len(msg.content or "") // 4)

        # Step 2: keep the system prompt (index 0) — it carries
        # identity + tools schema; trimming this confuses the model.
        kept = [history[0]] if history[0].role == "system" else []
        used = _approx_tokens(history[0]) if kept else 0
        # Step 3: walk tail-first, keeping recent turns until budget.
        tail: list = []
        for msg in reversed(history[1:] if kept else history):
            cost = _approx_tokens(msg)
            if used + cost > budget:
                break
            tail.append(msg)
            used += cost
        tail.reverse()
        self.history = kept + tail
        self.bus.emit(
            "system",
            f"trimmed history for local swap: {len(self.history)} "
            f"msgs, ~{used} tokens (budget {budget})",
        )

    def _swap_model_for_retry(self, model_id: str) -> None:
        """Hot-swap the provider's model mid-turn for an escalation retry.

        `model_id` is either a concrete Groq model id (e.g.
        `meta-llama/llama-4-maverick-17b-128e-instruct`) or the
        sentinel `"LOCAL"` which means switch to the configured local
        fallback provider.

        Best-effort: if the swap fails (provider doesn't support live
        model change, local model not installed, etc.) the caller
        logs and continues with the current provider. No raise.
        """
        from openbro.llm.fallback_provider import FallbackProvider

        if model_id == "LOCAL":
            # Switch to the local provider. Local llama.cpp models
            # default to 8K context vs Groq's 32K — after a few
            # escalation rounds the cloud history can be 12K+ tokens
            # which raises ValueError 'requested > context window'
            # the moment we send it. Trim aggressively first.
            try:
                from openbro.llm.router import create_provider

                cfg = load_config()
                local_cfg = cfg.get("providers", {}).get("local", {})
                local_ctx = int(local_cfg.get("n_ctx", 8192) or 8192)
                # Reserve ~1.5K for the response.
                self._trim_history_for_local_swap(local_ctx - 1500)
                self.provider = create_provider(provider_name="local")
                self.bus.emit(
                    "system",
                    f"escalator: swapped to local model "
                    f"({local_cfg.get('model', '?')}, ctx={local_ctx})",
                )
            except Exception as e:
                self.bus.emit("system", f"escalator: local swap failed — {e}")
            return

        # Concrete model id — update the underlying provider's model.
        # The FallbackProvider doesn't have a `.model` attr; it
        # delegates to its primary. Most providers store the model
        # as `self.model`; if not, the swap is silently skipped.
        target = self.provider
        if isinstance(target, FallbackProvider):
            target = getattr(target, "primary", target)
        if hasattr(target, "model"):
            target.model = model_id
            self.bus.emit("system", f"escalator: swapped model to {model_id}")

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
            provider=self.provider,
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

        # `pass_through_to_llm=True` playbooks (e.g. tech_research) inject
        # their output as a TOOL-LIKE context turn and let the LLM run
        # one more synthesis pass with the real source content. This
        # avoids returning a raw 'here are 3 web pages' dump to the
        # user — the model sees the sources, then writes a concrete
        # answer grounded in them with citations.
        if getattr(playbook, "pass_through_to_llm", False):
            # Append the playbook output as a 'system' note in history
            # so the LLM sees it for the NEXT chat() call but doesn't
            # echo it back. Then fall through (return None) so the
            # normal LLM loop runs.
            #
            # Two marker shapes are supported:
            #   [TRANSIENT_RESEARCH]  — tech_research (web sources)
            #   [TRANSIENT_PLAN]      — planner (planning instruction)
            # Both are pruned after the final answer via the same
            # post-synthesis prune logic in _chat_impl. Without
            # pruning, 15K-char research blocks accumulate in history
            # and every subsequent turn drags the same bloat → context
            # overflow, rate-limit cascade, fallback failure.
            #
            # If the playbook's response already starts with a
            # TRANSIENT marker, append as-is (the playbook controls
            # its own preamble). Otherwise wrap with the default
            # research preamble so older playbooks keep working.
            if response.lstrip().startswith("[TRANSIENT_"):
                content = response
            else:
                preamble = (
                    "[TRANSIENT_RESEARCH] "
                    f"Playbook `{playbook.name}` ran web research for "
                    "the user's question. Use the sources below to "
                    "write a concrete, specific answer. Cite source "
                    "URLs inline. Do NOT add 'I can't verify' or "
                    "'test on different devices' filler — the sources "
                    "are real, current docs."
                )
                content = preamble + "\n\n" + response
            self.history.append(Message(role="system", content=content))
            # Note: don't persist this to long-term memory — it's
            # one-turn context. The LLM's final answer will be the
            # persisted assistant message via the normal loop.
            return None

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
