"""Core Agent - the brain of OpenBro."""

import json
import re
import threading
from collections.abc import Iterator

from rich.console import Console

from openbro.core import session_memory
from openbro.core.activity import get_bus
from openbro.core.hooks import HookRunner
from openbro.core.permission_rules import PermissionRules
from openbro.core.permissions import PermissionGate, PermissionRequest
from openbro.core.tool_protocol import parse_tool_calls, render_tool_instructions
from openbro.core.workspace import detect_cached as detect_workspace
from openbro.llm.base import LLMResponse, Message
from openbro.llm.router import create_provider
from openbro.memory import MemoryManager
from openbro.memory.compaction import (
    SUMMARY_SYSTEM_PROMPT,
    CompactionConfig,
    CompactionResult,
    Compactor,
    estimate_tokens,
    make_summary_message,
    render_for_summary,
    repair_tool_pairs,
    split_head,
    split_turns,
)
from openbro.memory.instructions import instruction_files, load_instructions
from openbro.memory.instructions import signature as instruction_signature
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
    # Both-providers-failed → smooth, calm message instead of raw
    # ValueError. Captured 2026-05-30: user saw 'ValueError:
    # Requested tokens (11293) exceed context window of 8192' as the
    # entire response — felt like a crash, not a degraded answer.
    try:
        from openbro.llm.fallback_provider import _FallbackChainExhausted

        if isinstance(e, _FallbackChainExhausted):
            # Plain-text formatting (no backticks) — captured
            # 2026-05-31: backticks caused the markdown renderer
            # to collapse spaces around them, mangling the error
            # text into 'instantin organizationordiya' etc.
            return (
                "⏱️ Neither the primary nor the fallback model could be reached.\n"
                f"  Primary ({e.primary}): {e.primary_error[:120]}\n"
                f"  Fallback ({e.fallback}): {e.fallback_error[:120]}\n"
                "\n"
                "Fix options:\n"
                "  • Wait 30-60 seconds and try again — it usually recovers\n"
                "  • Run /recap to see where the task stands\n"
                "  • Switch the local model (openbro config set "
                "providers.local.model <name>) — bigger context helps"
            )
    except ImportError:
        pass

    msg = str(e)
    low = msg.lower()
    # Auth — recoverable by setting API key
    if "401" in msg or "unauthorized" in low or "invalid api key" in low:
        return (
            "❌ The API key is invalid.\n"
            "   Fix: `openbro config set providers.groq.api_key gsk_YOUR_KEY`\n"
            "   Get a new key: https://console.groq.com/keys"
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
            "⏱️  Rate limit reached, and every fallback model is exhausted too.\n"
            "   Fix: wait a minute and try again, or switch models with "
            "`openbro config set providers.groq.model openai/gpt-oss-20b`."
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
            "🌐 Could not connect to the LLM.\n"
            "   Fix: check your internet connection, or go offline with `openbro --offline` "
            "(needs a local model: `openbro model download llama3.1:8b`)."
        )
    # Tool call schema mismatch — model generated bad args
    if "tool call validation failed" in low or "failed to parse tool call" in low:
        return (
            "🔧 The model called a tool with the wrong arguments (schema mismatch).\n"
            "   Try the same request again — the fallback chain will use another model.\n"
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
        provider=None,
        tool_registry: ToolRegistry | None = None,
        role_prompt: str | None = None,
        max_steps: int | None = None,
        bus=None,
    ):
        """`provider`, `tool_registry`, `role_prompt` and `max_steps` are
        set when this agent runs as a sub-agent (the `task` tool): it then
        shares the parent's provider and a filtered view of its tools.
        `bus` gives the agent its own ActivityBus (one per web visitor);
        by default it uses the process-wide bus."""
        config = load_config()
        self.role_prompt = role_prompt
        self._max_steps = max_steps
        if provider is not None:
            self.provider = provider
        else:
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

        if isinstance(self.provider, FallbackProvider) and provider is None:

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
            # 'typing is not working' because the load competed with
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
        self.bus = bus if bus is not None else get_bus()

        self.tool_registry = tool_registry or ToolRegistry(config=config)
        # Inject memory into the memory tool so it uses this agent's user/session
        mem_tool = self.tool_registry.get_tool("memory")
        if isinstance(mem_tool, MemoryTool) and tool_registry is None:
            mem_tool._manager = self.memory
        # The task tool spawns sub-agents from this agent.
        task_tool = self.tool_registry.get_tool("task")
        if task_tool is not None and hasattr(task_tool, "_parent"):
            task_tool._parent = self

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
            self.permissions = PermissionGate(
                mode=mode, channel=channel, rules=PermissionRules.from_config(config)
            )
        # User hooks at fixed points of a turn (piece 8).
        self.hooks = HookRunner.from_config(config)
        self._hook_context = ""

        self.history: list[Message] = []

        self.base_system_prompt = config.get("agent", {}).get(
            "system_prompt",
            "You are OpenBro, a helpful, professional personal AI agent.",
        )
        self.tool_names = ", ".join(self.tool_registry.active_tools())
        # Whether the current system prompt carries the text tool-call
        # protocol. Tracked so a mid-session provider switch (native ⇄
        # text-only) rebuilds the prompt exactly once.
        self._prompt_text_tools = self._text_tool_mode()
        self.history.append(Message(role="system", content=self._build_system_prompt()))
        self.max_history = config.get("agent", {}).get("max_history", 50)
        # Compaction keeps history under a token budget by clearing old
        # tool output and summarising older turns — never a blind cut.
        compaction_cfg = CompactionConfig.from_config(config)
        n_ctx = self._local_context_window()
        if n_ctx:
            compaction_cfg.max_context_tokens = min(compaction_cfg.max_context_tokens, n_ctx)
        self.compactor = Compactor(compaction_cfg, summarize=self._summarize)

        self.last_language = "hinglish"
        self._lock = threading.RLock()  # serialize chat() across threads (REPL + voice)
        # Cumulative since process start — shown in REPL status bar so the
        # user knows their free-tier burn rate (Groq has TPM caps).
        self.session_tokens_in = 0
        self.session_tokens_out = 0
        self._turn_tokens_in = 0
        self._turn_tokens_out = 0

        if role_prompt is None:
            console.print(f"[dim]LLM: {self.provider.name()}[/dim]")
            self.bus.emit("system", f"agent ready: {self.provider.name()}")

    def _text_tool_mode(self) -> bool:
        """True when the provider has no native function calling
        (subscription CLI providers, local specialists). Tools are then described in
        the system prompt and parsed back out of the reply text."""
        try:
            return not self.provider.supports_tools()
        except Exception:
            return False

    def _build_system_prompt(self) -> str:
        """The STABLE part of the context — built once, not per turn.

        Everything here stays byte-identical across turns so providers
        with prompt caching reuse the prefix. Per-turn context
        (workspace, memory facts, language) goes in the reminder that
        `_messages_for_call` attaches to the latest user message.
        """
        parts = [
            self.base_system_prompt,
            f"\nTools available to you: {self.tool_names}. Use them whenever the task needs them.",
            self._world_facts_block(),
            self._intent_check_block(),
            self._instructions_block(),
        ]
        if self.role_prompt:
            parts.append("\n" + self.role_prompt)
        if self._prompt_text_tools:
            parts.append("\n" + render_tool_instructions(self.tool_registry.get_tools_schema()))
        deferred = self.tool_registry.deferred_index()
        if deferred:
            parts.append(
                "\n## MORE TOOLS (load before use)\n"
                "These exist but their parameters are not loaded yet. Call "
                "`tool_search` with select='<name>' to load one, then call it.\n"
                + "\n".join(deferred)
            )
        return "\n".join(p for p in parts if p)

    def refresh_system_prompt(self) -> None:
        """Rebuild the stable system prompt (tool list or provider changed)."""
        self._prompt_text_tools = self._text_tool_mode()
        content = self._build_system_prompt()
        if self.history and self.history[0].role == "system":
            self.history[0] = Message(role="system", content=content)
        else:
            self.history.insert(0, Message(role="system", content=content))

    def _reminder_block(self, lang: str | None) -> str:
        """Per-turn context: workspace, relevant memories, reply language."""
        parts = [self._workspace_block().strip()]
        query = next((m.content for m in reversed(self.history) if m.role == "user"), "")
        try:
            parts.append(self.memory.memory_block(query).strip())
        except Exception:
            pass
        parts.append(self._running_model_block())
        if lang:
            parts.append(language_instruction(lang).strip())
        if self._hook_context:
            parts.append("## FROM THE USER'S HOOKS\n" + self._hook_context)
        return "\n\n".join(p for p in parts if p)

    def _running_model_block(self) -> str:
        """Which LLM is answering, read from the live provider each turn.

        A model cannot know who serves it, so without this it guesses (or
        repeats a stale name). Per turn, not in the stable prompt, so a
        `/model` switch shows up on the very next message."""
        from openbro.llm.fallback_provider import FallbackProvider

        try:
            if isinstance(self.provider, FallbackProvider):
                main = self.provider.primary.name()
                backup = f" (backup if it fails: {self.provider.fallback.name()})"
            else:
                main, backup = self.provider.name(), ""
        except Exception:
            return ""
        return (
            f"## RUNNING MODEL\nOpenBro is running on {main}{backup}. If asked which model "
            "or LLM you are, say this — never another provider or model name."
        )

    def _messages_for_call(self) -> list[Message]:
        """History as sent to the provider: the latest user message gets
        a <system-reminder> with per-turn context appended.

        The reminder is NOT stored in `self.history`, so earlier turns
        stay unchanged and the cached prompt prefix stays valid.
        """
        reminder = self._reminder_block(self.last_language)
        if not reminder:
            return list(self.history)
        for i in range(len(self.history) - 1, -1, -1):
            msg = self.history[i]
            if msg.role == "user":
                messages = list(self.history)
                messages[i] = Message(
                    role="user",
                    content=f"{msg.content}\n\n<system-reminder>\n{reminder}\n</system-reminder>",
                )
                return messages
        return list(self.history)

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
        vision: no hard-coding — all reasoning comes from the LLM; the
        agent keeps working with the LLM until the problem is solved,
        and the plan is made at that moment.

        The agent layer stays minimal; intelligence is the LLM's job
        guided by these principles. No regex orchestration, no
        hardcoded decompose, no fixed planner instruction. Just
        clear rules the model applies to its own reasoning.
        """
        return (
            "\n## THINKING PRINCIPLES (apply these on every turn)\n"
            "\n"
            "### 1. ACT — don't lecture, don't ask permission\n"
            "NEVER write code in chat for the user to copy-paste."
            " NEVER ask 'do you want me to execute this?' or"
            " 'should I proceed?' — JUST DISPATCH the tool call.\n"
            "Allowed shape: ONE line stating what you'll do +"
            " immediate tool_call. Example:\n"
            "    'Reading both xlsx files via python tool now.'\n"
            "    [emit python tool_call with the pandas script]\n"
            "After the tool result lands, ONE line interpretation +"
            " next tool_call OR final answer. No 7-step text lecture"
            " before any action.\n"
            "For multi-step tasks, you may emit a brief numbered"
            " plan (1-5 lines, NOT paragraphs) AS PART OF dispatching"
            " step 1 — the plan is one line per step, then the first"
            " tool call follows in the SAME response. Never end a"
            " response with a plan and no tool call.\n"
            "Exception — choices only the user can make: when the request"
            " leaves open WHAT the result should be (e.g. 'make an image'"
            " without saying what it shows; 'write a letter' without who or"
            " why), ask ONE short question with 2-3 concrete suggestions"
            " and wait. Don't ask about details you can decide sensibly"
            " (size, format, file name).\n"
            "Finish: once the work succeeded, STOP calling tools — never"
            " redo a step that already worked. Reply briefly: what you made,"
            " where it is (full path), and one way to change it.\n"
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
            "### 4. Numeric / tabular questions → compute, don't guess\n"
            "When the user asks a quantitative question over a file"
            " (CSV, Excel, JSON, log), use the python tool to load"
            " the data and compute the actual answer. Don't eyeball a"
            " truncated snippet. Don't invent column names — work"
            " from the schema the tool gives you. If a column you"
            " expected isn't there, inspect the real columns and"
            " recover; don't hallucinate a fresh list.\n"
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
            " local: keep going until the problem is solved.\n"
            "\n"
            "### 7. Permission denied → escalate via `elevate`, don't fail silently\n"
            "When a tool (especially `shell`) returns 'Access denied',"
            " 'requires elevation', 'permission denied', or 'cannot"
            " access' for system paths (C:/Windows/Temp, HKLM,"
            " Recycle Bin, system services):\n"
            "  a. TELL the user briefly what you need (one line, in"
            " the user's language). Example: 'Cleaning C:\\Windows\\Temp"
            " needs admin rights — a UAC prompt will appear.'\n"
            "  b. CALL `elevate` with the same command (or its"
            " PowerShell equivalent) and a clear `reason` arg. UAC"
            " prompt will appear; user clicks Yes.\n"
            "  c. If `elevate` itself reports UAC denied / timeout,"
            " THEN tell the user: 'Please restart OpenBro as administrator.'\n"
            "Never silently swallow permission errors and report"
            " partial success — the user expects you to ask for"
            " elevation, not give up. Captured 2026-06-20: user said"
            " 'if you need permission, why don't you ask for it?'.\n"
            "\n"
            "### 8. Reply tone — short and professional, not a tutorial\n"
            "After the tools have done the work, the final assistant"
            " message goes to a human reading a chat. Keep it short"
            " and professional:\n"
            "  • 1-3 lines for most outcomes. Long bullet lists only"
            " when the user explicitly asked for steps.\n"
            "  • Lead with the result, not the journey. 'Temp cleared"
            " (1.2 GB freed). Recycle Bin still locked — chrome.exe"
            " is holding 200 MB.' Not 'It looks like the previous"
            " commands failed because…'\n"
            "  • No meta-narration: skip 'I am switching to native"
            " PowerShell', 'Let me try a different approach',"
            " 'attempting cleanup now'. Just dispatch the next tool."
            " Captured 2026-06-20: user asked for more professional"
            " replies.\n"
            "  • Never paste the tool's raw command line back at the"
            ' user (`shell.command(command="powershell -Command …")`)'
            " — that's debug output, not a conversation. Tools run;"
            " you summarise the outcome.\n"
            "  • Failure stays honest: 'Recycle Bin was not cleared —"
            " UAC permission was denied.' One line, no apology"
            " loop, no 'Would you like me to try again?' unless the"
            " next attempt is materially different.\n"
            "\n"
            "### 9. Memory — keep what lasts, nothing else\n"
            "Use the `memory` tool (action=save) when the user tells you"
            " something lasting about themselves (name, city, how they"
            " like replies), corrects how you work, or a durable"
            " project decision / deadline / location appears. One note"
            " per fact, short kebab-case name, one-line description."
            " For feedback and project notes add 'Why:' and 'How to"
            " apply:' lines. If a note on the topic exists, update it"
            " (same name) instead of adding a duplicate; delete a note"
            " that turned out wrong. Never save passwords, keys or"
            " OTPs, and never save what you can re-read from files."
            " The MEMORY block in the reminder shows what you already"
            " know — read a full note with action=read when its line"
            " looks relevant."
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

    def _instruction_files(self) -> list:
        from pathlib import Path

        from openbro.utils.config import get_config_dir

        try:
            return instruction_files(Path.cwd(), get_config_dir())
        except Exception:
            return []

    def _instructions_block(self) -> str:
        """User-written OPENBRO.md instructions (global + project)."""
        files = self._instruction_files()
        self._instructions_sig = instruction_signature(files)
        budget = int(
            (load_config().get("memory", {}) or {}).get("instructions_budget_tokens", 4000)
        )
        return "\n" + load_instructions(files, budget) if files else ""

    def _ensure_prompt_mode(self) -> None:
        """Rebuild the system prompt only when something it is built from
        changed: the provider's tool mode (e.g. `model switch` to a CLI
        provider) or an OPENBRO.md file (edited, or the cwd moved)."""
        if self._text_tool_mode() != self._prompt_text_tools or instruction_signature(
            self._instruction_files()
        ) != getattr(self, "_instructions_sig", ()):
            self.refresh_system_prompt()

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
    #                                     cap is appropriate. Hard
    #                                     tasks take 5-20+ loops;
    #                                     25 is enough headroom for that
    #                                     while still terminating on
    #                                     genuine loops.
    #
    #   MAX_TOOL_ITERATIONS_LOCAL = 80  — captured 2026-05-31 user
    #                                     ask: 'unlimited tokens lene
    #                                     do offline se...jab tak
    #                                     cheeje solve na ho'. Local runs in-
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

        if self._max_steps:
            return self._max_steps
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

        self.last_language = detect_language(user_input)
        self._ensure_prompt_mode()

        self._hook_context = ""
        if self.role_prompt is None and self.hooks.has("user_prompt_submit"):
            outcome = self.hooks.run(
                "user_prompt_submit",
                {"session_id": self.memory.session_id, "prompt": user_input},
            )
            self._report_hook_errors(outcome)
            if outcome.blocked:
                self.bus.emit("system", f"prompt blocked by hook: {outcome.message}")
                return f"⛔ Blocked by your hook: {outcome.message}"
            self._hook_context = outcome.context
        stop_continues = 0

        self.bus.emit("user", user_input, lang=self.last_language)
        self._append(Message(role="user", content=user_input))
        # Auto-record goals from goal-setting user turns. Persists to
        # SQLite so `openbro --resume` next session can pick up the
        # same goal. De-duplicates by text — same goal repeated isn't
        # re-recorded.
        self._maybe_record_goal(user_input)

        # Native function calling → send schemas. Text-only providers get
        # the tool protocol in the system prompt instead (tools=None) and
        # their replies are parsed for <tool_call> tags below.
        text_tools = self._prompt_text_tools
        tools = None if text_tools else self.tool_registry.get_tools_schema()
        known_tools = self.tool_registry.list_tools()
        # Schemas go out with every call but aren't in the history —
        # reserve their size out of the compaction budget.
        self.compactor.reserved_tokens = (
            len(json.dumps(tools, ensure_ascii=False)) // 4 if tools else 0
        )
        self._fit_context()
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

        # Track how many tool calls were dispatched this turn — used
        # by the reflection layer to detect "wrote code in chat instead
        # of calling python tool" (captured failure: zero tool calls
        # made AND response had fake Output: blocks).
        turn_tool_calls_made = 0
        empty_nudged = False

        # ─── Agent loop (was single-shot, which forced LLM to hallucinate
        # answers after one tool returned nothing). Loop till LLM stops
        # calling tools and emits a final text response (a ReAct loop).
        max_iterations = self._iteration_cap()
        for iteration in range(max_iterations):
            self.bus.emit(
                "llm_start",
                "calling LLM",
                step=iteration + 1,
                max_steps=max_iterations,
            )
            if iteration:
                # `tool_search` may have loaded more tools last round.
                if not text_tools:
                    tools = self.tool_registry.get_tools_schema()
                    self.compactor.reserved_tokens = (
                        len(json.dumps(tools, ensure_ascii=False)) // 4 if tools else 0
                    )
                # Tool results from the last round may have pushed the
                # history over budget.
                self._fit_context()
            llm_t0 = _time.monotonic()
            try:
                response = self.provider.chat(self._messages_for_call(), tools=tools)
            except Exception as e:
                return _friendly_error(e)

            if text_tools and not response.tool_calls:
                clean, calls = parse_tool_calls(response.content or "", known_tools)
                if calls:
                    response.tool_calls = calls
                    response.content = clean

            # Token accounting — every provider returns usage with at least
            # {input, output}. Cumulate per turn and emit so the UI can
            # show a running counter.
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

            if (
                not response.tool_calls
                and not (response.content or "").strip()
                and not empty_nudged
                and iteration + 1 < max_iterations
            ):
                # Some models (gpt-oss) end a turn with reasoning only and
                # no visible text. Ask once for the actual answer instead of
                # showing the user nothing.
                empty_nudged = True
                self.bus.emit("system", "empty reply — asking the model to answer")
                self._append(
                    Message(
                        role="user",
                        content="(Your last reply was empty. Answer my question now, "
                        "using the tool results above.)",
                    )
                )
                continue

            if not response.tool_calls:
                # Trust the model: no more tool calls = it's done. The
                # Thinking Principles in the system prompt cover
                # 'verify before claiming', 'retry on tool errors',
                # 'use python for tabular data' etc. — the model is
                # responsible for its own quality. No fabrication
                # detection regex, no escalation strategies; the user
                # explicitly asked to drop the patch layer
                # (2026-05-31).
                self._append(Message(role="assistant", content=response.content))
                if (
                    self.role_prompt is None
                    and self.hooks.has("stop")
                    and stop_continues < self.hooks.max_stop_continues
                ):
                    outcome = self.hooks.run(
                        "stop",
                        {"session_id": self.memory.session_id, "response": response.content},
                    )
                    self._report_hook_errors(outcome)
                    if outcome.blocked:
                        # The hook says the work isn't done — feed its
                        # message back and keep going.
                        stop_continues += 1
                        self.bus.emit("system", f"stop hook: {outcome.message}")
                        self._append(
                            Message(role="user", content=f"[hook feedback] {outcome.message}")
                        )
                        continue
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
            response = self.provider.chat(self._messages_for_call())
        except Exception as e:
            return f"Max iterations hit, fallback failed: {e}"
        if text_tools:
            # No more tool rounds — drop any stray tags from the answer.
            response.content, _ = parse_tool_calls(response.content or "", known_tools)
        self._append(Message(role="assistant", content=response.content))
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
        self._ensure_prompt_mode()
        self.bus.emit("user", user_input, lang=self.last_language)

        self._append(Message(role="user", content=user_input))
        self._fit_context()

        full_response = ""
        try:
            for token in self.provider.stream(self._messages_for_call()):
                full_response += token
                yield token
        except Exception as e:
            yield f"\nError: {e}"
            return

        self._append(Message(role="assistant", content=full_response))
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

        # pass_through_to_llm playbooks were deleted in the 2026-05-31
        # LLM-first refactor — the LLM uses the underlying tools
        # directly instead of going through a regex-orchestrated
        # injection layer. Kept the support attribute for any future
        # deterministic playbook that wants pass-through behaviour.
        if getattr(playbook, "pass_through_to_llm", False):
            self.history.append(Message(role="system", content=response))
            return None

        # Persist as a normal assistant turn so chat history stays consistent
        # — the LLM will see this on its next turn and won't be surprised.
        self._append(Message(role="assistant", content=response))
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
        self._append(
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

            # Pass args through compute_risk so a destructive `shell`
            # invocation (Remove-Item, rm -rf, Clear-RecycleBin, …)
            # gets upgraded from MODERATE to DANGEROUS and actually
            # triggers the permission modal. Read-only Get-* / dir /
            # ps stay MODERATE and run silently in normal mode.
            risk = self.tool_registry.get_risk(name, args=args)
            self.bus.emit(
                "tool_start",
                f"{name} ({risk})",
                tool=name,
                args=args,
                risk=risk,
            )

            blocked_by_hook = None
            if self.hooks.has("pre_tool_use"):
                outcome = self.hooks.run(
                    "pre_tool_use",
                    {"session_id": self.memory.session_id, "tool": name, "args": args},
                    tool=name,
                )
                self._report_hook_errors(outcome)
                if outcome.blocked:
                    blocked_by_hook = outcome.message

            if blocked_by_hook is not None:
                allowed = False
            else:
                req = PermissionRequest(tool=name, args=args, risk=risk)
                allowed = self.permissions.request(req)
            confirmed = allowed

            tool_t0 = _time.monotonic()
            if blocked_by_hook is not None:
                result = f"[{name}]: blocked by the user's hook — {blocked_by_hook}"
                self.bus.emit(
                    "tool_end",
                    result,
                    tool=name,
                    args=args,
                    ok=False,
                    elapsed=_time.monotonic() - tool_t0,
                    preview=result,
                )
            elif not allowed:
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
                if self.hooks.has("post_tool_use"):
                    outcome = self.hooks.run(
                        "post_tool_use",
                        {
                            "session_id": self.memory.session_id,
                            "tool": name,
                            "args": args,
                            "result": result[:20000],
                        },
                        tool=name,
                    )
                    self._report_hook_errors(outcome)
                    if outcome.context:
                        result = f"{result}\n\n[hook] {outcome.context}"
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
            self._append(
                Message(
                    role="tool",
                    content=result,
                    tool_call_id=tool_call.get("id", ""),
                )
            )

    # ─── history + context management (harness piece 2) ──────────────

    def _report_hook_errors(self, outcome) -> None:
        for err in outcome.errors:
            self.bus.emit("system", f"hook failed: {err}")

    def _append(self, msg: Message) -> None:
        """Append to the live history and record it in the transcript.

        Every message is recorded — tool calls and tool results included —
        so a resumed session sees exactly what the model saw.
        """
        self.history.append(msg)
        try:
            self.memory.record(msg)
        except Exception as e:  # never lose a turn over a storage hiccup
            self.bus.emit("system", f"transcript write failed: {e}")

    def _local_context_window(self) -> int | None:
        """n_ctx of a local model (directly or as the active primary)."""
        provider = getattr(self.provider, "primary", self.provider)
        engine = getattr(provider, "engine", None)
        for n_ctx in (getattr(engine, "n_ctx", None), getattr(provider, "n_ctx", None)):
            # Only a real positive int counts (mocks and odd configs don't).
            if isinstance(n_ctx, int) and not isinstance(n_ctx, bool) and n_ctx > 0:
                return n_ctx
        return None

    def _fit_context(
        self, force: bool = False, focus: str | None = None
    ) -> CompactionResult | None:
        """Compact the history if it is over budget (or when forced)."""
        result = self.compactor.fit(self.history, force=force, focus=focus)
        if result is None:
            return None
        self.history = result.history
        if result.summary:
            try:
                self.memory.save_summary(result.summary, result.summary_upto)
            except Exception as e:
                self.bus.emit("system", f"summary save failed: {e}")
        self.bus.emit(
            "system",
            result.describe(),
            compaction=True,
            tokens_before=result.tokens_before,
            tokens_after=result.tokens_after,
            summarised_turns=result.summarised_turns,
        )
        return result

    def _summarize(self, prior: str | None, messages: list[Message], focus: str | None) -> str:
        """One no-tools model call that folds older turns into the summary."""
        budget_chars = int(self.compactor.budget * 0.5) * 4
        transcript = render_for_summary(messages)
        if len(transcript) > budget_chars:
            transcript = "…[earlier part truncated]\n" + transcript[-budget_chars:]
        parts = []
        if prior:
            parts.append(f"Existing summary (merge it in, keep what still matters):\n{prior}")
        parts.append(f"Conversation to summarise:\n{transcript}")
        if focus:
            parts.append(f"The user asked to keep this in focus: {focus}")
        response = self.provider.chat(
            [
                Message(role="system", content=SUMMARY_SYSTEM_PROMPT),
                Message(role="user", content="\n\n".join(parts)),
            ],
            tools=None,
        )
        self.session_tokens_in += int(response.usage.get("input", 0) or 0)
        self.session_tokens_out += int(response.usage.get("output", 0) or 0)
        text, _ = parse_tool_calls(response.content or "", self.tool_registry.list_tools())
        limit = self.compactor.config.summary_max_tokens * 4 * 2
        return text.strip()[:limit]

    _EXTRACT_PROMPT = (
        "You maintain OpenBro's long-term memory about this user. Read the "
        "conversation and the existing memory index, then return ONLY a JSON "
        "array of operations, [] if nothing lasting came up.\n"
        'Save: {"op": "save", "name": "kebab-case", "description": "one line", '
        '"type": "user|feedback|project|reference", "body": "the fact; for '
        'feedback/project add Why: and How to apply: lines"}\n'
        'Delete: {"op": "delete", "name": "existing-name"}\n'
        "Save only lasting facts: who the user is, how they want you to work, "
        "durable project decisions, where things live. Reuse an existing name "
        "to update it. Never save secrets, passwords or one-off task details."
    )

    def _auto_extract_enabled(self) -> bool:
        mode = str((load_config().get("memory", {}) or {}).get("auto_extract", "off")).lower()
        if mode == "on":
            return True
        if mode == "auto":
            return self._local_context_window() is None  # cloud / CLI providers only
        return False

    def end_session(self) -> list[str]:
        """Optional: one model call at session end proposes memory updates.

        Off by default (`memory.auto_extract: off | on | auto`). Returns
        human-readable lines for what changed.
        """
        if not self._auto_extract_enabled():
            return []
        _, _, body = split_head(self.history)
        if not any(m.role == "user" for m in body):
            return []
        transcript = render_for_summary(body)[-int(self.compactor.budget * 0.5) * 4 :]
        index = "\n".join(n.index_line() for n in self.memory.notes.all()) or "(empty)"
        try:
            response = self.provider.chat(
                [
                    Message(role="system", content=self._EXTRACT_PROMPT),
                    Message(
                        role="user",
                        content=f"Existing memory index:\n{index}\n\nConversation:\n{transcript}",
                    ),
                ],
                tools=None,
            )
        except Exception as e:
            self.bus.emit("system", f"memory extract failed: {e}")
            return []
        return self._apply_memory_ops(response.content or "")

    def _apply_memory_ops(self, text: str) -> list[str]:
        from openbro.memory.notes import NOTE_TYPES, Note

        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            ops = json.loads(text[start : end + 1])
        except (TypeError, ValueError):
            return []
        done = []
        for op in ops if isinstance(ops, list) else []:
            if not isinstance(op, dict) or not op.get("name"):
                continue
            try:
                if op.get("op") == "save" and op.get("body"):
                    note_type = op.get("type") if op.get("type") in NOTE_TYPES else "user"
                    note = self.memory.save_note(
                        Note(
                            name=str(op["name"]),
                            description=str(op.get("description") or op["body"])[:200],
                            type=note_type,
                            body=str(op["body"]),
                        )
                    )
                    done.append(f"saved {note.name}")
                elif op.get("op") == "delete" and self.memory.forget(str(op["name"])):
                    done.append(f"deleted {op['name']}")
            except Exception:
                continue
        return done

    def compact(self, focus: str | None = None) -> CompactionResult | None:
        """`compact` command: summarise every complete turn now."""
        with self._lock:
            return self._fit_context(force=True, focus=focus)

    def context_report(self) -> dict:
        """Numbers for the `context` command."""
        head, summary, body = split_head(self.history)
        return {
            "budget": self.compactor.budget,
            "trigger": int(self.compactor.budget * self.compactor.config.trigger_ratio),
            "total": estimate_tokens(self.history),
            "system": estimate_tokens(head),
            "summary": estimate_tokens([summary]) if summary else 0,
            "conversation": estimate_tokens(body),
            "turns": sum(1 for t in split_turns(body) if t and t[0].role == "user"),
            "messages": len(body),
        }

    def resume(self, summary: str | None, messages: list[Message]) -> int:
        """Load a past session: system prompt + saved summary + messages
        after it (tool calls included), repaired and compacted to fit.
        Returns the number of messages restored."""
        with self._lock:
            restored = repair_tool_pairs(messages)
            self.history = [self.history[0]]
            if summary:
                self.history.append(make_summary_message(summary))
            self.history.extend(restored)
            self._fit_context()
            return len(restored)
