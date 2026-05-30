"""Interactive terminal REPL for OpenBro."""

import json as _json

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from openbro import __version__
from openbro.cli.wizard import needs_setup, run_wizard
from openbro.core.activity import get_bus
from openbro.core.agent import Agent
from openbro.utils.config import get_config_dir, load_config, save_config

console = Console()


_RISK_COLORS = {"safe": "green", "moderate": "yellow", "dangerous": "red"}


class _LiveStatus:
    """Bottom-pinned status line that ticks every 100ms.

    Shows: spinner · step N/M · current activity (thinking / running TOOL) ·
    elapsed for current phase · turn tokens · turn elapsed. Updates in place
    so the user sees a live clock instead of a frozen "OpenBro kaam kar
    raha hai" spinner. Tool-call panels print ABOVE the live area (Rich
    keeps the live block pinned at the bottom).
    """

    def __init__(self, agent, con: Console):
        import time as _time

        self.agent = agent
        self.con = con
        self.live = None
        self._unsub = None
        self._tick_thread = None
        self._tick_running = False
        self._time = _time
        self._state = {
            "step": 0,
            "max_steps": 10,
            "activity": "thinking...",
            "activity_kind": "llm",
            "activity_started": _time.monotonic(),
            "turn_started": _time.monotonic(),
        }

    def __enter__(self):
        from rich.live import Live

        now = self._time.monotonic()
        self._state["turn_started"] = now
        self._state["activity_started"] = now
        # transient=True so the live area DISAPPEARS when the turn ends —
        # the final answer + tool panels stay as scrollback, no leftover
        # ghost status line. refresh_per_second=10 gives smooth tick.
        self.live = Live(
            self._render(),
            console=self.con,
            refresh_per_second=10,
            transient=True,
        )
        self.live.start()
        from openbro.core.activity import get_bus

        self._unsub = get_bus().subscribe(self._on_event)
        # Wall-clock ticker — drives the elapsed counter without needing
        # bus events. Without this, the displayed elapsed would only
        # update when an event fires (so a slow LLM call would show
        # frozen 0.0s for seconds).
        import threading

        self._tick_running = True
        self._tick_thread = threading.Thread(target=self._tick, daemon=True)
        self._tick_thread.start()
        return self

    def __exit__(self, *exc):
        self._tick_running = False
        if self._unsub:
            try:
                self._unsub()
            except Exception:
                pass
            self._unsub = None
        if self.live:
            try:
                self.live.stop()
            except Exception:
                pass
            self.live = None

    def _tick(self) -> None:
        while self._tick_running:
            self._time.sleep(0.1)
            if not self.live:
                continue
            try:
                self.live.update(self._render())
            except Exception:
                pass

    def _on_event(self, ev) -> None:
        try:
            if ev.kind == "llm_start":
                self._state["step"] = ev.meta.get("step", 0)
                self._state["max_steps"] = ev.meta.get("max_steps", 10)
                self._state["activity"] = "thinking"
                self._state["activity_kind"] = "llm"
                self._state["activity_started"] = self._time.monotonic()
            elif ev.kind == "llm_end":
                self._state["activity"] = "processing result"
                self._state["activity_kind"] = "idle"
                self._state["activity_started"] = self._time.monotonic()
            elif ev.kind == "tool_start":
                tool = ev.meta.get("tool", "?")
                self._state["activity"] = f"running {tool}"
                self._state["activity_kind"] = "tool"
                self._state["activity_started"] = self._time.monotonic()
            elif ev.kind == "tool_end":
                self._state["activity"] = "thinking"
                self._state["activity_kind"] = "llm"
                self._state["activity_started"] = self._time.monotonic()
        except Exception:
            pass

    def _render(self):
        """Build the one-line live status. Called ~10x per second."""
        from rich.spinner import Spinner
        from rich.table import Table

        agent = self.agent
        now = self._time.monotonic()
        phase_elapsed = now - self._state["activity_started"]
        turn_elapsed = now - self._state["turn_started"]
        step = self._state["step"]
        max_steps = self._state["max_steps"]
        in_t = getattr(agent, "_turn_tokens_in", 0)
        out_t = getattr(agent, "_turn_tokens_out", 0)
        activity = self._state["activity"]
        kind = self._state["activity_kind"]

        # Color the activity by what kind of work it is.
        if kind == "llm":
            activity_style = "cyan"
        elif kind == "tool":
            activity_style = "yellow"
        else:
            activity_style = "magenta"

        # Compact one-line layout — table prevents column collapse on
        # narrow terminals. Spinner ticks via Rich's built-in animation.
        table = Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_row(
            Spinner("dots", style="cyan"),
            f"[dim]step {step}/{max_steps}[/dim]",
            f"[{activity_style}]{activity}[/{activity_style}] [dim]· {phase_elapsed:.1f}s[/dim]",
            f"[dim]turn:[/dim] [grey50]{turn_elapsed:.1f}s[/grey50]",
            f"[grey50]{in_t}↓ {out_t}↑[/grey50]",
        )
        return table


def _format_args(name: str, args: dict) -> tuple[str, str]:
    """Pick a syntax-highlight lexer and pretty-print args for display.

    `python` / `shell` get their actual code rendered as syntax-highlighted
    blocks (Claude Code parity — you see the bash command before it runs).
    Other tools get compact JSON. Returns (lexer, text).
    """
    if not isinstance(args, dict):
        return ("text", str(args))
    # Surface the actual code/command verbatim for the tools where it
    # matters most. Strip the line wrapping a JSON dump would impose.
    if name == "python" and "code" in args:
        return ("python", str(args["code"]))
    if name == "shell" and "command" in args:
        return ("powershell", str(args["command"]))
    if name == "file_ops" and args.get("action") == "write" and "content" in args:
        # Show write payload as text rather than embedded in JSON
        header = (
            f"# file_ops write: {args.get('path', '')}\n# {len(args.get('content', ''))} chars\n"
        )
        return ("text", header + str(args["content"])[:1500])
    try:
        return ("json", _json.dumps(args, indent=2, ensure_ascii=False)[:1500])
    except (TypeError, ValueError):
        return ("text", str(args)[:1500])


class _ToolCallRenderer:
    """Subscribe to ActivityBus events and render Claude-Code-style panels.

    Bus events drive the UI: `llm_start`/`llm_end` produce a thin status
    line with step + tokens + elapsed; `tool_start`/`tool_end` render a
    bordered panel with the tool name, formatted args (syntax-highlighted
    for python/shell), and a result preview.
    """

    def __init__(self, con: Console):
        self.con = con
        self._unsub = None
        self._open_step = 0

    def attach(self) -> None:
        self._unsub = get_bus().subscribe(self._on_event)

    def detach(self) -> None:
        if self._unsub:
            try:
                self._unsub()
            except Exception:
                pass
            self._unsub = None

    def _on_event(self, ev) -> None:
        try:
            if ev.kind == "llm_start":
                # Nothing to print — the live status bar at the bottom
                # of the screen already shows "step N · thinking · Xs".
                # Printing here would just duplicate the same info as
                # frozen scrollback.
                pass
            elif ev.kind == "llm_end":
                step = ev.meta.get("step", 0)
                in_t = ev.meta.get("input_tokens", 0)
                out_t = ev.meta.get("output_tokens", 0)
                elapsed = ev.meta.get("elapsed", 0)
                playbook = ev.meta.get("playbook")
                if playbook and in_t == 0 and out_t == 0:
                    # Playbook fast-path — 0 LLM tokens. Surface it as a
                    # distinct line so the user sees WHY this turn was
                    # instant + free.
                    self.con.print(
                        f"[dim]  ↳ [green]playbook[/green] [bold]{playbook}[/bold] "
                        f"· [green]0 tokens[/green] · {elapsed:.1f}s[/dim]",
                        highlight=False,
                    )
                else:
                    self.con.print(
                        f"[dim]  ↳ step {step} done · "
                        f"[cyan]{in_t}↓[/cyan] [magenta]{out_t}↑[/magenta] tokens · "
                        f"{elapsed:.1f}s[/dim]",
                        highlight=False,
                    )
            elif ev.kind == "tool_start":
                name = ev.meta.get("tool", "?")
                risk = ev.meta.get("risk", "safe")
                args = ev.meta.get("args", {})
                color = _RISK_COLORS.get(risk, "white")
                lexer, body = _format_args(name, args)
                if lexer == "json":
                    rendered = Syntax(
                        body, "json", theme="monokai", line_numbers=False, word_wrap=True
                    )
                elif lexer in ("python", "powershell"):
                    rendered = Syntax(
                        body, lexer, theme="monokai", line_numbers=False, word_wrap=True
                    )
                else:
                    rendered = body
                title = f"[bold {color}]⏵ {name}[/bold {color}] [dim]· {risk}[/dim]"
                self.con.print(
                    Panel(
                        rendered,
                        title=title,
                        title_align="left",
                        border_style=color,
                        padding=(0, 1),
                    )
                )
            elif ev.kind == "provider_fallback":
                # Primary LLM hit an error, falling back to local.
                # Surface it loud enough that the user knows why the
                # response is slower / from a smaller model, but
                # transient enough that it doesn't dominate the screen.
                primary = ev.meta.get("primary", "primary")
                fallback = ev.meta.get("fallback", "fallback")
                self.con.print(
                    f"\n[yellow]⚠ {primary} unavailable — switching to "
                    f"{fallback} for this turn.[/yellow]",
                    highlight=False,
                )
            elif ev.kind == "plan_started":
                tl = ev.meta.get("tasklist")
                if tl is not None:
                    step_count = len(tl.all())
                    title = f"[bold green]◆ Plan[/bold green] [dim]· {step_count} steps[/dim]"
                    self.con.print(
                        Panel(
                            tl.render_markdown(),
                            title=title,
                            title_align="left",
                            border_style="green",
                            padding=(0, 1),
                        )
                    )
            elif ev.kind == "plan_step_start":
                desc = ev.meta.get("task_id", "")  # not used in label
                step_desc = ev.text
                self.con.print(
                    f"\n[bold cyan]⏵ Step:[/bold cyan] {step_desc}",
                    highlight=False,
                )
                # silence unused-var warning
                _ = desc
            elif ev.kind == "plan_step_end":
                ok = ev.meta.get("ok", True)
                marker = "[green]✓[/green]" if ok else "[red]✗[/red]"
                self.con.print(
                    f"[dim]  {marker} step done · {ev.text}[/dim]",
                    highlight=False,
                )
            elif ev.kind == "plan_finished":
                tl = ev.meta.get("tasklist")
                if tl is not None:
                    done, total = tl.progress()
                    ok = tl.succeeded()
                    color = "green" if ok else "yellow"
                    glyph = "✓" if ok else "⚠"
                    self.con.print(
                        f"\n[bold {color}]{glyph} Plan finished[/bold {color}] "
                        f"[dim]· {done}/{total} steps[/dim]\n",
                        highlight=False,
                    )
            elif ev.kind == "tool_end":
                name = ev.meta.get("tool", "?")
                ok = ev.meta.get("ok", True)
                preview = ev.meta.get("preview", "") or ""
                full_length = ev.meta.get("full_length", len(preview))
                elapsed = ev.meta.get("elapsed", 0)
                # Trim very long results — the LLM gets the full thing,
                # the user just needs a confirmation glance.
                preview_short = preview
                if len(preview_short) > 600:
                    preview_short = preview_short[:600] + f"\n… (+{full_length - 600} chars)"
                marker = "[green]✓[/green]" if ok else "[red]✗[/red]"
                if preview_short.strip():
                    self.con.print(
                        Panel(
                            preview_short,
                            title=f"{marker} [dim]{name} result · {elapsed:.1f}s[/dim]",
                            title_align="left",
                            border_style="grey42",
                            padding=(0, 1),
                        ),
                        highlight=False,
                    )
                else:
                    self.con.print(
                        f"  {marker} [dim]{name} done · {elapsed:.1f}s · (no output)[/dim]",
                        highlight=False,
                    )
        except Exception:
            # Never let UI rendering crash the agent loop.
            pass


COMMANDS = [
    "help",
    "exit",
    "quit",
    "config",
    "model",
    "models",
    "pull",
    "tools",
    "playbooks",
    "jobs",
    "wait",
    "kill",
    "workspace",
    "fallback",
    "storage",
    "audit",
    "memory",
    "remember",
    "forget",
    "sessions",
    "skills",
    "show",
    "hide",
    "boss",
    "activity",
    "voice",
    "cloud",
    "brain",
    "clear",
    "reset",
]
completer = WordCompleter(COMMANDS, ignore_case=True)


def _render_response(con: Console, text: str) -> None:
    """Render an assistant response with Markdown formatting.

    Falls back to plain print if Markdown rendering fails (e.g. very
    long output that the parser chokes on). Claude Code parity:
    code fences become syntax-highlighted blocks, lists / tables /
    headings render properly instead of as raw backticks.
    """
    from rich.markdown import Markdown

    if not text:
        return
    try:
        # Markdown() takes care of code blocks, lists, headings.
        con.print(Markdown(text, code_theme="monokai"))
    except Exception:
        con.print(text, highlight=False)


def print_banner():
    # Minimal, professional banner — Claude-Code-style. No bold caps, no
    # ASCII glyphs at headline size. One muted intro line + cwd + hint,
    # all in `dim` so the eye lands on the user's input first. Status
    # bar at the bottom carries the persistent details (model, voice,
    # tokens, shortcuts).
    import os as _os

    cwd = _os.getcwd()
    if len(cwd) > 60:
        cwd = "…" + cwd[-58:]
    console.print()
    # Subtle one-line header — small, soft, no shouty colors.
    console.print(
        f"  [grey50]◆[/grey50] [white]openbro[/white] "
        f"[grey42]v{__version__}[/grey42]  "
        f"[grey42]·[/grey42]  [grey42]{cwd}[/grey42]"
    )
    console.print("  [grey42]Type your question, or `/help` for commands. `exit` to quit.[/grey42]")
    console.print()


def _make_status_bar(agent, get_voice_state):
    """Build a prompt_toolkit bottom_toolbar callable.

    Claude Code parity: a thin status line always visible under the prompt
    showing model, voice/boss state, cumulative token usage, and slash
    commands. Called fresh on every keystroke so toggling voice or
    completing a turn updates the bar immediately.
    """
    from prompt_toolkit.formatted_text import HTML

    def _bar():
        cfg = load_config()
        provider = cfg.get("llm", {}).get("provider", "?")
        model = cfg.get("llm", {}).get("model", "?")
        # Model names like 'meta-llama/llama-4-scout-17b-16e-instruct' get
        # noisy in a single-line toolbar — trim the org/version cruft.
        short_model = model.split("/")[-1]
        if len(short_model) > 32:
            short_model = short_model[:29] + "…"
        voice_state = "on" if get_voice_state() else "off"
        boss_state = (
            "boss"
            if getattr(agent, "permissions", None)
            and getattr(agent.permissions, "mode", "") == "boss"
            else "auto"
        )
        # Cumulative session token spend — Claude Code-like at-a-glance
        # gauge for how much the conversation has used.
        tok_in = getattr(agent, "session_tokens_in", 0)
        tok_out = getattr(agent, "session_tokens_out", 0)
        tok_total = tok_in + tok_out

        def _fmt_tok(n: int) -> str:
            if n < 1000:
                return str(n)
            if n < 10000:
                return f"{n / 1000:.1f}k"
            return f"{n // 1000}k"

        # HTML-escape the model name — it can contain '<' / '&' that
        # prompt_toolkit's HTML parser would choke on.
        from html import escape as _esc

        # Fallback indicator: shows whether the local backup is ready
        # to take over if the cloud primary craps out. Glyphs:
        #   ✓ ready    ⏳ downloading    — disabled
        try:
            from openbro.utils.local_llm_setup import is_fallback_ready

            fb_ready = is_fallback_ready()
        except Exception:
            fb_ready = False
        fb_text = "fallback ✓" if fb_ready else "fallback ⏳"
        # If cfg sets fallback to nothing, suppress the indicator entirely.
        if not (cfg.get("llm", {}) or {}).get("fallback"):
            fb_text = ""

        return HTML(
            f" <ansigray>◆</ansigray> <ansigray>{_esc(provider)}/{_esc(short_model)}</ansigray>"
            f"  <ansigray>·</ansigray>  <ansigray>voice {voice_state}</ansigray>"
            f"  <ansigray>·</ansigray>  <ansigray>{boss_state}</ansigray>"
            f"  <ansigray>·</ansigray>  <ansigray>"
            f"{_fmt_tok(tok_in)}↓ {_fmt_tok(tok_out)}↑ ({_fmt_tok(tok_total)})</ansigray>"
            + (f"  <ansigray>·</ansigray>  <ansigray>{fb_text}</ansigray>" if fb_text else "")
            + "  <ansigray>·</ansigray>  <ansigray>/help /tools /voice /model /quit</ansigray>"
        )

    return _bar


def start_repl(resume_session: str | None = None):
    """Launch the interactive REPL.

    `resume_session`: when set, the agent loads a past session before
    accepting input. Pass 'latest' to grab the most recent, or a
    specific session ID to target one. None starts a fresh session.
    """
    # First-run wizard
    if needs_setup():
        run_wizard()

    print_banner()

    # Background activity log so 'activity.log' is always populated
    from openbro.cli.activity_panel import start_background_log

    log_path = start_background_log()
    console.print(f"[dim]Activity log: {log_path}[/dim]\n")

    config_dir = get_config_dir()
    history_file = config_dir / "history.txt"

    agent = Agent()

    # Auto-download the fallback model if it's missing. Returns instantly
    # — work runs in a background JobRegistry job so the REPL is fully
    # responsive while the file pulls. Status bar carries a 'fallback:
    # ⏳ downloading…' indicator that turns to '✓ ready' on completion.
    try:
        from openbro.utils.local_llm_setup import ensure_fallback_ready_async

        ensure_fallback_ready_async()
    except Exception:
        pass  # never let setup break the REPL

    # Resume a past session if asked. We do this BEFORE the prompt
    # session is constructed so the status bar shows the right token
    # count from the start.
    if resume_session is not None:
        _resume_previous_session(agent, resume_session)

    # Auto-start voice listener if configured (before the session is built
    # so the status bar can already reflect 'voice on').
    cfg = load_config()
    if cfg.get("voice", {}).get("auto_start"):
        _start_voice(agent)

    # The status bar reads the live voice listener state via a closure so
    # toggling voice on/off updates the toolbar without re-creating the
    # session.
    status_bar = _make_status_bar(agent, lambda: _voice_listener is not None)

    # Subscribe a rich renderer to the ActivityBus — shows each LLM step,
    # tool call (with syntax-highlighted python/shell args), and result
    # preview. This is the Claude-Code-style "see what's happening"
    # surface above the chat box.
    progress = _ToolCallRenderer(console)
    progress.attach()

    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
        complete_while_typing=False,
        bottom_toolbar=status_bar,
    )

    # Daily LLM-update check (non-blocking, 24h cooldown). If a meaningfully
    # better model is available, prompt the user once.
    _maybe_suggest_llm_upgrade(agent, cfg)

    # Register atexit handler so even if REPL crashes / window is X-closed,
    # we still try to stop the voice listener and release the mic.
    import atexit

    atexit.register(_stop_voice)

    try:
        while True:
            try:
                # Claude-Code-style prompt: a subtle thin separator above the
                # input so each turn has visual breathing room, then a cyan
                # `›` glyph. Status bar (bottom_toolbar) sits below the input
                # showing model + voice + boss + shortcut hints.
                from prompt_toolkit.formatted_text import ANSI

                console.print()
                console.rule(style="grey23")
                # Dim grey prompt glyph — Claude-style. Loud cyan was
                # competing with the user's typed text for attention.
                user_input = session.prompt(ANSI("\x1b[38;5;245m›\x1b[0m ")).strip()

                if not user_input:
                    continue

                if user_input.lower() in ("exit", "quit", "bye"):
                    console.print("[dim cyan]◆ session ended.[/dim cyan]")
                    break

                # Handle built-in commands
                if _handle_command(user_input, agent):
                    continue

                # Tool-capable providers need the full agent loop. The streaming
                # path cannot currently surface structured tool calls reliably.
                #
                # Render the assistant response as Markdown so code blocks, lists,
                # tables, etc. format correctly — Claude Code does the same. A
                # plain console.print() leaves '```python\n...' as raw backticks.
                # (_render_response handles the Markdown import — see helper.)
                response = ""
                turn_in_before = agent.session_tokens_in
                turn_out_before = agent.session_tokens_out
                import time as _t

                turn_t0 = _t.monotonic()
                try:
                    if agent.provider.supports_tools():
                        # _LiveStatus replaces console.status — gives a
                        # bottom-pinned bar that ticks every 100ms with
                        # the current phase (thinking / running TOOL),
                        # phase elapsed, turn elapsed, and live token
                        # counters. Tool panels and step lines print
                        # ABOVE the live bar as scrollback.
                        with _LiveStatus(agent, console):
                            response = agent.chat(user_input)
                        # ⏺ (filled circle) for the assistant marker — Claude
                        # Code's same glyph. Cleaner than ◆ when output is
                        # immediately followed by code or markdown.
                        console.print("\n[grey50]⏺[/grey50] ", end="")
                        _render_response(console, response)
                    else:
                        console.print("\n[grey50]⏺[/grey50] ", end="")
                        for token in agent.stream_chat(user_input):
                            console.print(token, end="", highlight=False)
                            response += token
                except Exception:
                    with _LiveStatus(agent, console):
                        response = agent.chat(user_input)
                    console.print("\n[grey50]⏺[/grey50] ", end="")
                    _render_response(console, response)
                # Turn footer — Claude Code-style "what just happened" line.
                # in/out token delta + wall-clock for this single turn so
                # the user can spot a runaway loop (huge in tokens) or a
                # network stall (high elapsed, low tokens).
                turn_in = agent.session_tokens_in - turn_in_before
                turn_out = agent.session_tokens_out - turn_out_before
                turn_elapsed = _t.monotonic() - turn_t0
                if turn_in or turn_out:
                    console.print(
                        f"  [dim]· {turn_in}↓ {turn_out}↑ tokens · {turn_elapsed:.1f}s[/dim]",
                        highlight=False,
                    )
                console.print("")

            except KeyboardInterrupt:
                console.print("\n[bold cyan]OpenBro interrupted.[/bold cyan]")
                break
            except EOFError:
                break
    finally:
        # Hard cleanup so mic / threads release even if an exception escapes.
        _stop_voice()


def _handle_command(cmd: str, agent: Agent) -> bool:
    """Handle built-in commands. Returns True if command was handled."""
    cmd_lower = cmd.lower().strip()

    if cmd_lower == "help":
        _show_help()
        return True

    if cmd_lower == "config":
        _show_config()
        return True

    if cmd_lower.startswith("config set "):
        _set_config(cmd[11:].strip())
        return True

    if cmd_lower == "model":
        _show_model(agent)
        return True

    if cmd_lower.startswith("model "):
        _switch_model(cmd[6:].strip(), agent)
        return True

    if cmd_lower == "tools":
        _show_tools(agent)
        return True

    if cmd_lower in ("playbooks", "playbook"):
        _show_playbooks(agent)
        return True

    if cmd_lower in ("jobs", "tasks"):
        _show_jobs()
        return True

    if cmd_lower == "workspace":
        _show_workspace()
        return True

    if cmd_lower in ("fallback", "fallback status"):
        _show_fallback_status()
        return True

    if cmd_lower == "fallback download":
        _trigger_fallback_download()
        return True

    if cmd_lower == "fallback test":
        _test_fallback(agent)
        return True

    if cmd_lower.startswith("wait "):
        _wait_job(cmd[5:].strip())
        return True

    if cmd_lower.startswith("kill "):
        _kill_job(cmd[5:].strip())
        return True

    if cmd_lower == "storage":
        _show_storage()
        return True

    if cmd_lower == "storage move":
        _move_storage()
        return True

    if cmd_lower == "pull":
        _pull_model()
        return True

    if cmd_lower.startswith("pull "):
        _pull_model(cmd[5:].strip())
        return True

    if cmd_lower == "models":
        _show_models()
        return True

    if cmd_lower == "audit":
        _show_audit()
        return True

    if cmd_lower == "memory":
        _show_memory(agent)
        return True

    if cmd_lower.startswith("remember "):
        _remember(cmd[9:].strip(), agent)
        return True

    if cmd_lower.startswith("forget "):
        _forget(cmd[7:].strip(), agent)
        return True

    if cmd_lower == "sessions":
        _show_sessions(agent)
        return True

    if cmd_lower == "skills":
        _show_skills(agent)
        return True

    if cmd_lower == "show":
        _start_panel()
        return True

    if cmd_lower == "hide":
        _stop_panel()
        return True

    if cmd_lower == "activity":
        from openbro.cli.activity_panel import print_recent

        print_recent(30)
        return True

    if cmd_lower in ("boss", "boss on"):
        agent.permissions.mode = "boss"
        console.print(
            "[bold yellow]Boss mode ON.[/bold yellow] "
            "Har tool call ke liye permission maangi jayegi."
        )
        return True

    if cmd_lower == "boss off":
        agent.permissions.mode = "normal"
        console.print("[green]Boss mode OFF. Sirf dangerous tools confirm honge.[/green]")
        return True

    if cmd_lower in ("voice", "voice on"):
        _start_voice(agent)
        return True

    if cmd_lower in ("voice config", "voice status"):
        _show_voice_config()
        return True

    if cmd_lower == "voice cloud on":
        _set_voice_cloud(True)
        return True

    if cmd_lower == "voice cloud off":
        _set_voice_cloud(False)
        return True

    if cmd_lower == "brain" or cmd_lower == "brain stats":
        _brain_stats()
        return True

    if cmd_lower == "brain skills":
        _brain_skills()
        return True

    if cmd_lower == "brain learnings":
        _brain_learnings()
        return True

    if cmd_lower == "brain update":
        _brain_update()
        return True

    if cmd_lower.startswith("brain export"):
        path = cmd[len("brain export") :].strip() or "openbro_brain_backup.tar.gz"
        _brain_export(path)
        return True

    if cmd_lower.startswith("brain import "):
        path = cmd[len("brain import ") :].strip()
        _brain_import(path)
        return True

    if cmd_lower == "brain reset":
        _brain_reset()
        return True

    if cmd_lower == "brain check-llm":
        _brain_check_llm(agent)
        return True

    if cmd_lower == "voice off":
        _stop_voice()
        return True

    if cmd_lower == "voice test":
        _voice_test()
        return True

    if cmd_lower.startswith("model add "):
        from openbro.cli.model_manager import add_model

        add_model(cmd[10:].strip())
        return True

    if cmd_lower.startswith("model remove ") or cmd_lower.startswith("model rm "):
        from openbro.cli.model_manager import remove_model

        target = cmd.split(maxsplit=2)[2].strip()
        remove_model(target)
        return True

    if cmd_lower.startswith("model switch "):
        from openbro.cli.model_manager import switch_model

        target = cmd[13:].strip()
        if switch_model(target):
            from openbro.llm.router import create_provider

            agent.provider = create_provider()
            console.print(f"[green]Active provider: {agent.provider.name()}[/green]")
        return True

    if cmd_lower == "model list" or cmd_lower == "model ls":
        from openbro.cli.model_manager import list_available

        list_available()
        return True

    if cmd_lower == "clear":
        console.clear()
        print_banner()
        return True

    if cmd_lower == "reset":
        agent.history = [agent.history[0]]  # Keep system prompt only
        console.print("[yellow]Chat history cleared.[/yellow]\n")
        return True

    return False


def _show_help():
    table = Table(title="OpenBro Commands", border_style="cyan")
    table.add_column("Command", style="bold cyan")
    table.add_column("Description")

    table.add_row("help", "Show this help")
    table.add_row("config", "Show current configuration")
    table.add_row("config set <key> <val>", "Update config (e.g. config set llm.provider openai)")
    table.add_row("model", "Show current LLM model")
    table.add_row("model <name>", "Switch model (e.g. model gpt-4o)")
    table.add_row("tools", "List available tools")
    table.add_row("playbooks", "List pre-built workflows (0 LLM tokens when matched)")
    table.add_row("jobs / tasks", "Show running + recent background jobs")
    table.add_row("wait <id>", "Block on a background job, show result panel")
    table.add_row("kill <id>", "Cancel a running background job (cooperative)")
    table.add_row("workspace", "Show detected cwd / project / git context")
    table.add_row("fallback", "Show local-fallback model status (ready / downloading)")
    table.add_row("fallback download", "Manually kick off the fallback model download")
    table.add_row("fallback test", "Send a tiny query through the local fallback")
    table.add_row("models", "List downloaded offline models")
    table.add_row("pull", "Download a new offline model (interactive)")
    table.add_row("pull <model>", "Download specific model (e.g. pull llama3.2:3b)")
    table.add_row("storage", "Show storage usage and paths")
    table.add_row("storage move", "Move data to a different drive/folder")
    table.add_row("audit", "Show recent tool execution log")
    table.add_row("memory", "Show stored facts and memory stats")
    table.add_row("remember <key> <val>", "Save a fact (e.g. remember name Brijesh)")
    table.add_row("forget <key>", "Delete a fact")
    table.add_row("sessions", "List past conversation sessions")
    table.add_row("skills", "List installed skills (github, gmail, calendar, notion, youtube)")
    table.add_row("show", "Open the live activity panel (agent's environment)")
    table.add_row("hide", "Close the live activity panel (agent runs in background)")
    table.add_row("activity", "Print last 30 activity events (one-shot)")
    table.add_row("boss / boss off", "Toggle Boss mode — agent asks permission for every tool")
    table.add_row("voice / voice off", "Toggle voice listener (mic always-on inside REPL)")
    table.add_row("voice config", "Show STT model, wake words, and cloud STT status")
    table.add_row("voice cloud on/off", "Use Groq cloud STT first, offline Whisper as fallback")
    table.add_row("voice test", "Quick mic test - records + transcribes + prints")
    table.add_row("brain / brain stats", "Show brain stats (skills, memory, profile)")
    table.add_row("brain skills", "List learned skills with usage + success rate")
    table.add_row("brain learnings", "Recent reflection events (signal + delta)")
    table.add_row("brain update", "Pull community patterns + new skills")
    table.add_row("brain export <path>", "Backup brain to a tar.gz")
    table.add_row("brain import <path>", "Restore brain from a tar.gz")
    table.add_row("brain check-llm", "Force LLM upgrade check (skip 24h cooldown)")
    table.add_row("brain reset", "Wipe the entire brain (with confirmation)")
    table.add_row("model list", "List all available models with status")
    table.add_row("model add <name>", "Add a model (downloads Ollama OR stores API key)")
    table.add_row("model switch <name>", "Switch active model (offers to remove old offline)")
    table.add_row("model remove <name>", "Uninstall offline model OR clear cloud key")
    table.add_row("clear", "Clear screen")
    table.add_row("reset", "Clear chat history")
    table.add_row("exit / quit", "Exit OpenBro")

    console.print(table)
    console.print("\n[dim]Or just type naturally and OpenBro will use tools when needed.[/dim]\n")


def _show_config():
    config = load_config()
    import yaml

    config_str = yaml.dump(config, default_flow_style=False)
    console.print(Syntax(config_str, "yaml", theme="monokai"))


def _set_config(args: str):
    parts = args.split(maxsplit=1)
    if len(parts) != 2:
        console.print("[red]Usage: config set <key.path> <value>[/red]")
        console.print("[dim]Example: config set llm.provider anthropic[/dim]")
        return

    key_path, value = parts
    config = load_config()
    keys = key_path.split(".")

    # Navigate to the right nested dict
    obj = config
    for k in keys[:-1]:
        if k not in obj or not isinstance(obj[k], dict):
            obj[k] = {}
        obj = obj[k]

    # Convert value types
    if value.lower() == "true":
        value = True
    elif value.lower() == "false":
        value = False
    elif value.isdigit():
        value = int(value)

    obj[keys[-1]] = value
    save_config(config)
    console.print(f"[green]Set {key_path} = {value}[/green]\n")


def _show_model(agent: Agent):
    console.print(f"[cyan]Current model:[/cyan] {agent.provider.name()}\n")


def _switch_model(model_name: str, agent: Agent):
    config = load_config()

    # Check if it's a provider switch (e.g. "anthropic" or "openai")
    if model_name in ("local", "ollama", "anthropic", "openai", "groq", "google", "deepseek"):
        # 'ollama' kept as alias — both route to the in-process local engine
        if model_name == "ollama":
            model_name = "local"
        config["llm"]["provider"] = model_name
        save_config(config)
        agent.provider = __import__(
            "openbro.llm.router", fromlist=["create_provider"]
        ).create_provider(model_name)
        console.print(
            f"[green]Switched to provider: {model_name} ({agent.provider.name()})[/green]\n"
        )
    else:
        # Just change the model name
        config["llm"]["model"] = model_name
        save_config(config)
        from openbro.llm.router import create_provider

        agent.provider = create_provider()
        console.print(f"[green]Switched model to: {agent.provider.name()}[/green]\n")


def _show_tools(agent: Agent):
    table = Table(title="Available Tools", border_style="cyan")
    table.add_column("Tool", style="bold")
    table.add_column("Risk", justify="center")
    table.add_column("Description")

    risk_styles = {"safe": "green", "moderate": "yellow", "dangerous": "red"}

    for schema in agent.tool_registry.get_tools_schema():
        name = schema["name"]
        risk = agent.tool_registry.get_risk(name)
        style = risk_styles.get(risk, "white")
        table.add_row(name, f"[{style}]{risk}[/{style}]", schema.get("description", ""))

    console.print(table)
    console.print(
        "\n[dim]Risk: safe = read-only, "
        "moderate = modifies files/opens apps, "
        "dangerous = system-level changes[/dim]\n"
    )


def _resume_previous_session(agent: Agent, session_hint: str) -> None:
    """Load a past session into the agent's memory + chat history.

    `session_hint` is either 'latest' (sentinel from --resume with no arg)
    or a specific session ID. We hydrate working memory via the existing
    MemoryManager.load_session, then push each past message into
    agent.history so the LLM sees the conversation on next chat() call.
    """
    sessions = agent.memory.list_sessions()
    if not sessions:
        console.print("[yellow]No past sessions to resume. Starting fresh.[/yellow]\n")
        return

    if session_hint == "latest":
        target_id = sessions[0]["session_id"]
        label = "most recent"
    else:
        # Exact match first; if none, allow prefix matching for short IDs.
        match = next(
            (s for s in sessions if s["session_id"] == session_hint),
            None,
        )
        if match is None:
            match = next(
                (s for s in sessions if s["session_id"].startswith(session_hint)),
                None,
            )
        if match is None:
            console.print(
                f"[red]No session matching `{session_hint}`.[/red] "
                f"Run `sessions` after launch to see options.\n"
            )
            return
        target_id = match["session_id"]
        label = target_id

    try:
        agent.memory.load_session(target_id)
    except Exception as e:
        console.print(f"[red]Failed to load session {target_id}: {e}[/red]\n")
        return

    # Replay the past turns into agent.history. The system prompt at
    # history[0] stays — we only append from the resumed conversation.
    from openbro.llm.base import Message as _Message

    resumed = agent.memory.working()
    replayed = 0
    for msg in resumed:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        agent.history.append(_Message(role=role, content=msg.get("content", "")))
        replayed += 1

    console.print(
        f"[green]Resumed session[/green] [bold]{target_id}[/bold] "
        f"[dim]({label}, {replayed} messages loaded)[/dim]\n"
    )


def _show_fallback_status():
    """Show whether the local fallback model is downloaded + ready.

    The status bar already shows ✓/⏳, but `fallback` gives the full
    detail — model name, file size, expected vs actual disk path, any
    download job that's currently running.
    """
    from openbro.utils.config import load_config
    from openbro.utils.local_llm_setup import MODELS, is_fallback_ready, models_dir

    cfg = load_config()
    fb = (cfg.get("llm", {}) or {}).get("fallback")
    if not fb:
        console.print(
            "[dim]No fallback configured. Set with: `config set llm.fallback local`[/dim]\n"
        )
        return
    if fb != "local":
        console.print(
            f"[green]Fallback configured: {fb} (cloud-to-cloud, no download needed)[/green]\n"
        )
        return

    model_name = (cfg.get("providers", {}).get("local") or {}).get("model")
    if not model_name:
        model_name = cfg.get("llm", {}).get("model", "llama3.2:3b")
    info = MODELS.get(model_name) or {}
    ready = is_fallback_ready(model_name)

    table = Table(title="Fallback Status", border_style="cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Configured fallback", "local")
    table.add_row("Model", model_name)
    if info.get("size"):
        table.add_row("Size", info["size"])
    if info.get("ram"):
        table.add_row("RAM needed", info["ram"])
    target = models_dir() / info["file"] if info.get("file") else None
    if target:
        table.add_row("Expected path", str(target))
    status = "[green]✓ ready[/green]" if ready else "[yellow]⏳ not downloaded yet[/yellow]"
    table.add_row("Status", status)
    console.print(table)

    # Show any active download job
    try:
        from openbro.core.jobs import JobRegistry

        for j in JobRegistry.get().list_all():
            if j.meta.get("kind") == "fallback_download" and j.is_alive():
                elapsed = j.elapsed() or 0
                console.print(
                    f"\n[yellow]⏳ Download running:[/yellow] job `{j.id}` "
                    f"({elapsed:.0f}s elapsed). `wait {j.id}` to block."
                )
                break
    except Exception:
        pass
    if not ready:
        console.print(
            "\n[dim]Run `fallback download` to start a manual download, "
            "or wait — auto-download is queued on every REPL start.[/dim]\n"
        )


def _trigger_fallback_download():
    """Manually kick the auto-download (idempotent — does nothing if
    already running or already complete)."""
    from openbro.utils.local_llm_setup import ensure_fallback_ready_async, is_fallback_ready

    if is_fallback_ready():
        console.print("[green]Fallback already ready — nothing to do.[/green]\n")
        return
    job_id = ensure_fallback_ready_async()
    if job_id is None:
        console.print(
            "[yellow]No fallback configured for download.[/yellow] "
            "Set with `config set llm.fallback local`.\n"
        )
        return
    console.print(
        f"[green]Started background download job `{job_id}`.[/green] "
        f"Track with `jobs` or `wait {job_id}`.\n"
    )


def _test_fallback(agent):
    """Send a tiny query directly through the fallback provider to verify
    it works end-to-end."""
    from openbro.llm.base import Message
    from openbro.llm.fallback_provider import FallbackProvider

    if not isinstance(agent.provider, FallbackProvider):
        console.print(
            "[yellow]Fallback not active in current session.[/yellow] "
            "Restart the REPL after a fresh download to enable.\n"
        )
        return
    console.print("[dim]Sending test query through fallback…[/dim]")
    try:
        resp = agent.provider.fallback.chat(
            [Message(role="user", content="Reply with exactly: ok")]
        )
        console.print(f"[green]Fallback OK[/green] → {resp.content[:200]}\n")
    except Exception as e:
        console.print(f"[red]Fallback failed: {e}[/red]\n")


def _show_workspace():
    """Show the workspace context the agent has detected for this cwd."""
    from openbro.core.workspace import detect

    ctx = detect()
    table = Table(title="Workspace Context", border_style="cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("cwd", ctx.cwd)
    if ctx.project_name:
        table.add_row("project", ctx.project_name)
    if ctx.is_git_repo:
        dirty = " (dirty)" if ctx.git_dirty else ""
        table.add_row("git branch", f"{ctx.git_branch or '?'}{dirty}")
    if ctx.is_python_project:
        table.add_row("python project", "yes")
    if ctx.is_node_project:
        table.add_row("node project", "yes")
    if ctx.recent_files:
        table.add_row("recent files", ", ".join(ctx.recent_files))
    for key, val in (ctx.hints or {}).items():
        table.add_row(f"hint:{key}", str(val))
    console.print(table)
    console.print(
        "\n[dim]Drop a `.openbro/workspace.yaml` in this folder to "
        "add custom hints that get injected into every prompt.[/dim]\n"
    )


def _show_jobs():
    """List every background job — alive ones at the top."""
    from openbro.core.jobs import JobRegistry

    registry = JobRegistry.get()
    jobs = registry.list_all()
    if not jobs:
        console.print("[dim]No background jobs.[/dim]\n")
        return
    # alive first, then finished by recency
    alive = [j for j in jobs if j.is_alive()]
    done = sorted(
        [j for j in jobs if not j.is_alive()],
        key=lambda j: j.finished_at or 0,
        reverse=True,
    )
    table = Table(title="Background Jobs", border_style="cyan")
    table.add_column("ID", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Elapsed", justify="right")
    table.add_column("Label")
    for j in alive + done[:10]:
        elapsed = j.elapsed()
        elapsed_str = f"{elapsed:.1f}s" if elapsed is not None else "-"
        style = {
            "running": "yellow",
            "queued": "dim",
            "done": "green",
            "failed": "red",
            "cancelled": "dim",
        }.get(j.status.value, "white")
        table.add_row(
            j.id,
            f"[{style}]{j.status.value}[/{style}]",
            elapsed_str,
            j.label,
        )
    console.print(table)
    console.print(
        f"\n[dim]Tip: `wait <id>` to block until finished, "
        f"`kill <id>` to cancel. Alive: {registry.alive_count()}.[/dim]\n"
    )


def _wait_job(job_id: str):
    """Block on a background job and show its final result."""
    from openbro.core.jobs import JobRegistry

    if not job_id:
        console.print("[red]Usage: wait <job-id>[/red]\n")
        return
    registry = JobRegistry.get()
    job = registry.get_job(job_id)
    if job is None:
        console.print(f"[red]No job `{job_id}`[/red]\n")
        return
    if job.is_alive():
        console.print(f"[dim]Waiting for job `{job.id}` ({job.label})...[/dim]")
        with console.status("[dim]running…[/dim]", spinner="dots"):
            registry.wait(job.id)
        job = registry.get_job(job_id)
    if job is None:
        console.print("[red]Job vanished.[/red]\n")
        return
    elapsed = job.elapsed()
    elapsed_str = f"{elapsed:.1f}s" if elapsed is not None else "-"
    status_color = {
        "done": "green",
        "failed": "red",
        "cancelled": "yellow",
    }.get(job.status.value, "white")
    console.print(
        f"\n[bold {status_color}]{job.status.value.upper()}[/bold {status_color}] "
        f"[dim]· job `{job.id}` · {elapsed_str}[/dim]"
    )
    if job.error:
        console.print(f"[red]Error: {job.error}[/red]\n")
    if job.result:
        console.print(
            Panel(
                job.result[:4000],
                title=f"[dim]{job.label}[/dim]",
                title_align="left",
                border_style=status_color,
                padding=(0, 1),
            )
        )


def _kill_job(job_id: str):
    """Request cancellation on a running background job."""
    from openbro.core.jobs import JobRegistry

    if not job_id:
        console.print("[red]Usage: kill <job-id>[/red]\n")
        return
    registry = JobRegistry.get()
    if registry.cancel(job_id):
        console.print(
            f"[yellow]Cancel requested for `{job_id}` — "
            "thread will exit when it next checks the flag.[/yellow]\n"
        )
    else:
        job = registry.get_job(job_id)
        if job is None:
            console.print(f"[red]No job `{job_id}`[/red]\n")
        else:
            console.print(f"[dim]Job `{job_id}` already finished ({job.status.value}).[/dim]\n")


def _show_playbooks(agent: Agent):
    """List every registered playbook + its trigger count.

    Playbooks are pre-built workflows that handle common intents without
    an LLM call. Match -> direct tool sequence -> templated response.
    """
    table = Table(title="Playbooks (LLM-free fast path)", border_style="green")
    table.add_column("Name", style="bold")
    table.add_column("Triggers", justify="right")
    table.add_column("Keywords", justify="right")
    table.add_column("Description")

    pbs = agent.playbook_registry.list_all()
    if not pbs:
        console.print("[dim]No playbooks loaded.[/dim]\n")
        return

    for pb in pbs:
        info = pb.info()
        table.add_row(
            info["name"],
            str(info["triggers"]),
            str(info["keywords"]),
            info["description"],
        )
    console.print(table)
    enabled = getattr(agent, "playbooks_enabled", True)
    status = (
        "[green]enabled[/green]"
        if enabled
        else "[yellow]disabled (config: agent.playbooks_enabled)[/yellow]"
    )
    console.print(
        f"\n[dim]Status: {status} · matches use 0 LLM tokens, return "
        "instantly with a templated response.[/dim]\n"
    )


def _show_storage():
    from openbro.utils.storage import (
        format_size,
        get_available_drives,
        get_storage_paths,
        get_storage_size,
    )

    paths = get_storage_paths()
    sizes = get_storage_size()

    table = Table(title="Storage Info", border_style="cyan")
    table.add_column("Item", style="bold")
    table.add_column("Path")
    table.add_column("Size", justify="right")

    for key, path in paths.items():
        size = format_size(sizes.get(key, 0))
        table.add_row(key, str(path), size)

    console.print(table)

    # Show drive info
    drives = get_available_drives()
    if drives:
        console.print()
        dtable = Table(title="Drives", border_style="dim")
        dtable.add_column("Drive", style="bold")
        dtable.add_column("Free", justify="right")
        dtable.add_column("Total", justify="right")
        dtable.add_column("Used", justify="right")

        for d in drives:
            if d["used_percent"] < 80:
                style = "green"
            elif d["used_percent"] < 95:
                style = "yellow"
            else:
                style = "red"
            dtable.add_row(
                d["name"],
                f"{d['free_gb']} GB",
                f"{d['total_gb']} GB",
                f"[{style}]{d['used_percent']}%[/{style}]",
            )

        console.print(dtable)
    console.print()


def _move_storage():
    from rich.prompt import Prompt

    from openbro.utils.storage import get_storage_paths, migrate_storage, set_storage_path

    current = get_storage_paths()
    console.print(f"[dim]Current data location: {current['base']}[/dim]")

    new_path = Prompt.ask("Enter new path for OpenBro data")
    if not new_path:
        return

    from rich.prompt import Confirm

    if Confirm.ask(f"Move all data from {current['base']} to {new_path}?", default=False):
        try:
            migrate_storage(str(current["base"]), new_path)
            set_storage_path(new_path)
            console.print(f"[green]Data moved to: {new_path}[/green]\n")
        except Exception as e:
            console.print(f"[red]Move failed: {e}[/red]\n")
    else:
        console.print("[dim]Cancelled.[/dim]\n")


def _pull_model(model_name: str | None = None):
    from openbro.utils.local_llm_setup import (
        download_model,
        ensure_llama_cpp_python,
        show_model_picker,
    )

    if not ensure_llama_cpp_python():
        return

    if not model_name:
        model_name = show_model_picker()
    if model_name:
        download_model(model_name)
    console.print()


def _show_models():
    from openbro.utils.local_llm_setup import MODELS, list_installed, models_dir

    md = models_dir()
    installed = list_installed()
    if not installed:
        console.print(
            "[yellow]No local models downloaded yet.[/yellow] [dim]Use 'pull' to grab one.[/dim]\n"
        )
        console.print(f"[dim]Models dir: {md}[/dim]\n")
        return

    table = Table(title="Downloaded Models", border_style="cyan")
    table.add_column("Model", style="bold")
    table.add_column("Size", justify="right")
    for f in installed:
        size_gb = f.stat().st_size / 1e9
        table.add_row(f.name, f"{size_gb:.1f} GB")
    console.print(table)
    console.print(
        f"\n[dim]Total: {len(installed)} model(s) at {md}. "
        f"Catalogue ({len(MODELS)} models): use 'pull' to add more.[/dim]\n"
    )


def _show_audit():
    from openbro.utils.audit import get_recent_logs

    logs = get_recent_logs(limit=20)
    if not logs:
        console.print("[dim]No audit log entries yet.[/dim]\n")
        return

    risk_styles = {"safe": "green", "moderate": "yellow", "dangerous": "red"}

    table = Table(title="Recent Tool Executions (last 20)", border_style="cyan")
    table.add_column("Time", style="dim", width=19)
    table.add_column("Tool", style="bold")
    table.add_column("Risk", justify="center")
    table.add_column("Confirmed", justify="center")
    table.add_column("Result", overflow="fold")

    for entry in logs:
        ts = entry.get("timestamp", "")[:19].replace("T", " ")
        risk = entry.get("risk", "safe")
        style = risk_styles.get(risk, "white")
        confirmed = "yes" if entry.get("confirmed") else "auto"
        table.add_row(
            ts,
            entry.get("tool", "?"),
            f"[{style}]{risk}[/{style}]",
            confirmed,
            entry.get("result_preview", "")[:80],
        )

    console.print(table)
    console.print()


def _show_memory(agent: Agent):
    facts = agent.memory.all_facts()
    stats = agent.memory.stats()

    console.print(
        f"[cyan]Memory stats:[/cyan] "
        f"{stats['facts']} facts, "
        f"{stats['messages']} messages, "
        f"{stats['sessions']} sessions"
    )
    console.print(f"[dim]Current session: {agent.memory.session_id}[/dim]\n")

    if not facts:
        console.print("[dim]No facts stored yet. Use 'remember <key> <value>'.[/dim]\n")
        return

    table = Table(title="Stored Facts", border_style="cyan")
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_column("Category", style="dim")
    table.add_column("Updated", style="dim", overflow="fold")

    for f in facts[:30]:
        table.add_row(
            f["key"],
            f["value"][:80],
            f.get("category", "general"),
            f.get("updated_at", "")[:19].replace("T", " "),
        )

    console.print(table)
    console.print()


def _remember(args: str, agent: Agent):
    parts = args.split(maxsplit=1)
    if len(parts) != 2:
        console.print("[red]Usage: remember <key> <value>[/red]")
        console.print("[dim]Example: remember name Brijesh[/dim]\n")
        return
    key, value = parts
    agent.memory.remember(key, value)
    console.print(f"[green]Remembered:[/green] {key} = {value}\n")


def _forget(key: str, agent: Agent):
    if not key:
        console.print("[red]Usage: forget <key>[/red]\n")
        return
    if agent.memory.forget(key):
        console.print(f"[green]Forgot:[/green] {key}\n")
    else:
        console.print(f"[yellow]Nothing to forget for: {key}[/yellow]\n")


def _show_skills(agent: Agent):
    info = agent.tool_registry.skills_info()
    if not info:
        console.print("[dim]No skills loaded.[/dim]\n")
        return

    table = Table(title="Installed Skills", border_style="cyan")
    table.add_column("Skill", style="bold")
    table.add_column("Configured", justify="center")
    table.add_column("Tools")
    table.add_column("Description")

    for s in info:
        configured = "[green]✓[/green]" if s["configured"] else "[yellow]✗[/yellow]"
        table.add_row(
            f"{s['name']} v{s['version']}",
            configured,
            ", ".join(s["tools"]),
            s["description"],
        )

    console.print(table)
    console.print(
        "\n[dim]Configure skills via 'config set skills.<name>.<key> <value>'. "
        "Drop custom skills in ~/.openbro/skills/.[/dim]\n"
    )


_active_panel = None
_voice_listener = None
_voice_thread = None


def _start_voice(agent: Agent):
    """Start a background voice listener thread alongside the REPL."""
    global _voice_listener, _voice_thread

    if _voice_thread and _voice_thread.is_alive():
        console.print("[dim]Voice already listening.[/dim]")
        return

    try:
        from openbro.voice.listener import VoiceListener
        from openbro.voice.tts import TextToSpeech
    except Exception as e:
        console.print(f"[red]Voice deps missing: {e}[/red]")
        console.print("[dim]Install: pip install 'openbro[voice]'[/dim]")
        return

    cfg = load_config()
    voice_cfg = cfg.get("voice", {}) or {}
    tts = TextToSpeech(voice=voice_cfg.get("tts_voice", "en-IN-NeerjaNeural"))

    try:
        _voice_listener = VoiceListener(
            mode=voice_cfg.get("mode", "continuous"),
            wake_words=voice_cfg.get("wake_words"),
            stop_phrases=voice_cfg.get("stop_phrases"),
            stt_model=voice_cfg.get("stt_model", "small"),
            stt_language=voice_cfg.get("stt_language"),
            stt_device=voice_cfg.get("stt_device", "cpu"),
            stt_compute_type=voice_cfg.get("stt_compute_type", "int8"),
            stt_beam_size=int(voice_cfg.get("stt_beam_size", 5)),
            stt_vad_filter=bool(voice_cfg.get("stt_vad_filter", True)),
            chunk_seconds=float(voice_cfg.get("chunk_seconds", 8.0)),
            silence_threshold=float(voice_cfg.get("silence_threshold", 0.003)),
            silence_seconds=float(voice_cfg.get("silence_seconds", 0.8)),
            speak_replies=voice_cfg.get("speak_replies", True),
            assistant_name="OpenBro",
            ack_phrases=voice_cfg.get("ack_phrases"),
        )
    except Exception as e:
        console.print(f"[red]Voice listener init failed: {e}[/red]")
        return

    _voice_listener.tts = tts

    def _handle(text: str) -> str:
        try:
            from openbro.utils.language import voice_for

            reply = agent.chat(text)
            tts.voice = voice_for(agent.last_language)
            return reply
        except Exception as ex:
            return f"Voice error: {ex}"

    _voice_listener.on_transcript = _handle

    # on_heard fires for EVERY transcript. In continuous mode every utterance
    # is treated as a command (has_wake=True), so we just echo what was heard.
    # In wake_word mode we explain why a non-matching transcript was ignored —
    # the #1 voice complaint is "voice kaam nahi kar rha" which is usually a
    # wake-word miss, not a real bug.
    is_continuous = _voice_listener.mode == "continuous"

    def _on_heard(text: str, has_wake: bool) -> None:
        if is_continuous:
            console.print(f"\n[dim]🎤[/dim] {text}", highlight=False)
        elif has_wake:
            console.print(f"\n[dim]🎤 [cyan]wake[/cyan]:[/dim] {text}", highlight=False)
        else:
            console.print(
                f"\n[dim]🎤 heard (no wake word — ignored):[/dim] {text}\n"
                "[dim]   say 'hey openbro <command>' or 'ok bro <command>' "
                "to act on voice[/dim]",
                highlight=False,
            )

    _voice_listener.on_heard = _on_heard

    import threading

    _voice_thread = threading.Thread(target=_voice_listener.run, daemon=True)
    _voice_thread.start()
    if is_continuous:
        console.print(
            "[green]🎤 Voice ACTIVE — continuous mode.[/green] "
            "[dim]Bol bhai, har baat command hai. Stop: 'voice off' / "
            "'bye bro' / Ctrl+C.[/dim]"
        )
    else:
        console.print(
            "[green]Voice listening.[/green] "
            "[dim]Wake words: hey openbro, ok openbro. Type 'voice off' to stop.[/dim]"
        )


def _stop_voice():
    global _voice_listener, _voice_thread
    if not _voice_listener:
        console.print("[dim]Voice is not active.[/dim]")
        return
    _voice_listener.stop()
    _voice_listener = None
    _voice_thread = None
    console.print("[yellow]Voice listening stopped.[/yellow]")


def _show_voice_config():
    cfg = load_config()
    voice_cfg = cfg.get("voice", {}) or {}
    groq_key = bool((cfg.get("providers", {}).get("groq", {}) or {}).get("api_key"))
    table = Table(title="Voice Config", border_style="cyan")
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_row("enabled", str(voice_cfg.get("enabled", True)))
    table.add_row("mode", str(voice_cfg.get("mode", "continuous")))
    table.add_row("auto_start", str(voice_cfg.get("auto_start", False)))
    table.add_row("wake_words", ", ".join(voice_cfg.get("wake_words") or []))
    table.add_row("stop_phrases", ", ".join(voice_cfg.get("stop_phrases") or []))
    table.add_row("ack_phrases", " | ".join(voice_cfg.get("ack_phrases") or []))
    table.add_row("stt_model", str(voice_cfg.get("stt_model", "small")))
    table.add_row("stt_language", str(voice_cfg.get("stt_language")))
    table.add_row("stt_beam_size", str(voice_cfg.get("stt_beam_size", 5)))
    table.add_row("chunk_seconds", str(voice_cfg.get("chunk_seconds", 8.0)))
    table.add_row("silence_threshold", str(voice_cfg.get("silence_threshold", 0.003)))
    table.add_row("use_cloud_stt", str(voice_cfg.get("use_cloud_stt", False)))
    table.add_row("cloud_stt_model", str(voice_cfg.get("cloud_stt_model", "")))
    table.add_row("groq_api_key", "set" if groq_key else "missing")
    console.print(table)
    console.print(
        "\n[dim]If local Whisper hears badly, set a Groq key and run "
        "'voice cloud on'. Offline Whisper remains fallback.[/dim]\n"
    )


def _set_voice_cloud(enabled: bool):
    cfg = load_config()
    cfg.setdefault("voice", {})["use_cloud_stt"] = enabled
    save_config(cfg)
    if enabled:
        has_key = bool((cfg.get("providers", {}).get("groq", {}) or {}).get("api_key"))
        console.print("[green]Cloud STT enabled.[/green] Offline Whisper remains fallback.")
        if not has_key:
            console.print(
                "[yellow]Groq API key missing.[/yellow] "
                "Set it with: config set providers.groq.api_key YOUR_KEY"
            )
    else:
        console.print("[green]Cloud STT disabled. Using local Whisper only.[/green]")


def _voice_test():
    """Quick mic + STT sanity check: record a phrase, transcribe, print."""
    try:
        from openbro.voice.listener import VoiceListener
    except Exception as e:
        console.print(f"[red]Voice deps missing: {e}[/red]")
        console.print("[dim]Install: pip install 'openbro[voice]'[/dim]")
        return

    cfg = load_config()
    voice_cfg = cfg.get("voice", {}) or {}
    try:
        v = VoiceListener(
            wake_words=voice_cfg.get("wake_words"),
            stt_model=voice_cfg.get("stt_model", "small"),
            stt_language=voice_cfg.get("stt_language"),
            stt_device=voice_cfg.get("stt_device", "cpu"),
            stt_compute_type=voice_cfg.get("stt_compute_type", "int8"),
            stt_beam_size=int(voice_cfg.get("stt_beam_size", 5)),
            stt_vad_filter=bool(voice_cfg.get("stt_vad_filter", True)),
            chunk_seconds=float(voice_cfg.get("test_seconds", voice_cfg.get("chunk_seconds", 8.0))),
            silence_threshold=float(voice_cfg.get("silence_threshold", 0.003)),
            silence_seconds=float(voice_cfg.get("silence_seconds", 0.8)),
            speak_replies=False,
            assistant_name="OpenBro",
            ack_phrases=voice_cfg.get("ack_phrases"),
        )
    except Exception as e:
        console.print(f"[red]Voice init failed: {e}[/red]")
        return

    console.print(
        "[bold cyan]Voice test:[/bold cyan] "
        "[dim]speak now - recording until silence or timeout...[/dim]"
    )
    try:
        text = v.listen_once()
    except Exception as e:
        console.print(f"[red]Recording failed: {e}[/red]")
        return

    if not text:
        console.print(
            "[yellow]No speech detected.[/yellow] "
            "[dim]Check: mic plugged in, Windows mic permission allowed, "
            "spoke loud enough, no other app holding the mic.[/dim]"
        )
        return
    console.print(f"[green]Heard:[/green] {text}")
    console.print(
        "[dim]If that looks right, voice mode will work. "
        "Say 'hey openbro <command>' "
        "(e.g. 'hey openbro notepad open kar') to use it.[/dim]"
    )


def _start_panel():
    global _active_panel
    if _active_panel is not None:
        console.print("[dim]Activity panel already running.[/dim]")
        return
    from openbro.cli.activity_panel import ActivityPanel

    _active_panel = ActivityPanel()
    _active_panel.start()
    console.print("[green]🤖 Activity panel started.[/green] [dim]Type 'hide' to close it.[/dim]")


def _stop_panel():
    global _active_panel
    if _active_panel is None:
        console.print("[dim]No active panel to hide.[/dim]")
        return
    _active_panel.stop()
    _active_panel = None
    console.print("[yellow]Panel hidden. Agent still running in background.[/yellow]")


def _show_sessions(agent: Agent):
    sessions = agent.memory.list_sessions()
    if not sessions:
        console.print("[dim]No past sessions found.[/dim]\n")
        return

    table = Table(title="Recent Sessions", border_style="cyan")
    table.add_column("Session ID", style="bold")
    table.add_column("Channel")
    table.add_column("Started")
    table.add_column("Last Activity")

    for s in sessions[:20]:
        table.add_row(
            s["session_id"],
            s.get("channel", "cli"),
            s.get("started_at", "")[:19].replace("T", " "),
            s.get("last_activity", "")[:19].replace("T", " "),
        )

    console.print(table)
    console.print()


# ─── Brain commands ───────────────────────────────────────────────


def _get_brain():
    from openbro.brain import Brain
    from openbro.brain.memory import SemanticMemory
    from openbro.brain.skills import SkillRegistry

    brain = Brain.load()
    if not getattr(brain, "memory", None):
        brain.memory = SemanticMemory(brain.storage.memory_db_path)
    if not getattr(brain, "skills", None):
        brain.skills = SkillRegistry(brain.storage.skills_dir)
    return brain


def _brain_stats():
    brain = _get_brain()
    stats = brain.stats()
    table = Table(title="Brain Stats", border_style="cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for key in (
        "version",
        "brain_id",
        "created_at",
        "last_update",
        "interaction_count",
        "patterns",
        "skills",
        "size_kb",
    ):
        v = stats.get(key, "-")
        if key == "size_kb":
            v = f"{v} KB"
        table.add_row(key, str(v))
    console.print(table)
    profile = stats.get("profile_summary", "")
    if profile:
        console.print(f"\n[dim]{profile}[/dim]\n")


def _brain_skills():
    brain = _get_brain()
    skills = brain.skills.list()
    if not skills:
        console.print("[dim]No skills learned yet. They appear as you use OpenBro.[/dim]\n")
        return
    table = Table(title="Learned Skills", border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Triggers")
    table.add_column("Uses", justify="right")
    table.add_column("Success rate", justify="right")
    for s in skills:
        total = s.success_count + s.fail_count
        rate = f"{(s.success_count / total * 100):.0f}%" if total else "-"
        table.add_row(
            s.name,
            ", ".join((s.triggers or [])[:3]),
            str(total),
            rate,
        )
    console.print(table)
    console.print()


def _brain_learnings():
    brain = _get_brain()
    events = brain.storage.read_learnings(limit=20)
    if not events:
        console.print("[dim]No learning events yet.[/dim]\n")
        return
    for ev in events:
        ts = ev.get("ts", "")[:19].replace("T", " ")
        kind = ev.get("type", "?")
        signal = ev.get("signal", "")
        skill = ev.get("used_skill") or ""
        delta = ev.get("delta", 0.0)
        line = f"[dim]{ts}[/dim] [bold]{kind}[/bold]"
        if signal:
            line += f" signal={signal}"
        if skill:
            line += f" skill={skill}"
        if delta:
            line += f" Δ={delta:+.2f}"
        console.print(line)
    console.print()


def _brain_update():
    brain = _get_brain()
    console.print("[dim]Pulling community manifest...[/dim]")
    result = brain.update()
    if result.get("ok"):
        console.print(f"[green]{result.get('message', 'updated')}[/green]")
    else:
        console.print(f"[yellow]{result.get('message', 'no update')}[/yellow]")
    console.print()


def _brain_export(path: str):
    brain = _get_brain()
    try:
        out = brain.export(path)
        size_kb = out.stat().st_size // 1024
        console.print(f"[green]Brain exported: {out} ({size_kb} KB)[/green]\n")
    except Exception as e:
        console.print(f"[red]Export failed: {e}[/red]\n")


def _brain_import(path: str):
    if not path:
        console.print("[red]Usage: brain import <path/to/backup.tar.gz>[/red]\n")
        return
    from rich.prompt import Confirm

    if not Confirm.ask(f"Replace current brain with contents of {path}?", default=False):
        console.print("[dim]Cancelled.[/dim]\n")
        return
    brain = _get_brain()
    try:
        brain.import_from(path, replace=True)
        console.print(f"[green]Brain restored from {path}[/green]\n")
    except Exception as e:
        console.print(f"[red]Import failed: {e}[/red]\n")


def _brain_reset():
    from rich.prompt import Confirm

    if not Confirm.ask(
        "Wipe brain (memory, skills, learnings, profile)? This cannot be undone.",
        default=False,
    ):
        console.print("[dim]Cancelled.[/dim]\n")
        return
    brain = _get_brain()
    import shutil

    try:
        shutil.rmtree(brain.storage.dir, ignore_errors=True)
        console.print("[green]Brain wiped. A fresh brain starts on next chat.[/green]\n")
    except Exception as e:
        console.print(f"[red]Reset failed: {e}[/red]\n")


def _brain_check_llm(agent):
    """Manual trigger of the daily LLM upgrade check."""
    brain = _get_brain()
    cfg = load_config()
    current = (
        cfg.get("llm", {}).get("provider", ""),
        cfg.get("llm", {}).get("model", ""),
    )
    suggestion = brain.check_for_better_llm(current, cfg, force=True)
    if suggestion:
        console.print(
            f"[bold yellow]Better LLM available:[/bold yellow] "
            f"{suggestion['provider']}/{suggestion['model']} "
            f"(score {suggestion['score']})"
        )
        console.print(f"[dim]Switch with: model switch {suggestion['provider']}[/dim]\n")
    else:
        console.print(f"[green]You're already on a top model: {current[0]}/{current[1]}[/green]\n")


def _maybe_suggest_llm_upgrade(agent, cfg):
    """Non-blocking 24h-cooldown LLM upgrade suggestion at REPL startup."""
    try:
        brain = _get_brain()
        current = (
            cfg.get("llm", {}).get("provider", ""),
            cfg.get("llm", {}).get("model", ""),
        )
        suggestion = brain.check_for_better_llm(current, cfg, force=False)
        if suggestion:
            console.print(
                f"\n[bold yellow]Naya LLM available:[/bold yellow] "
                f"{suggestion['provider']}/{suggestion['model']} "
                f"(score {suggestion['score']} vs current "
                f"{current[0]}/{current[1]})"
            )
            console.print(f"[dim]Switch anytime: 'model switch {suggestion['provider']}'[/dim]\n")
    except Exception:
        # Never block startup on the upgrade check
        pass
