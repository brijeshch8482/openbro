"""Permission gate - asks the user before risky tool calls.

Modes:
- 'normal'   : ask only for DANGEROUS tools (legacy behavior)
- 'boss'     : ask for EVERY tool (user is the boss)
- 'auto'     : never ask (only for trusted automated runs)

Channels:
- 'cli'      : Rich Confirm prompt
- 'voice'    : TTS asks the question, then listens for yes/no
- 'silent'   : auto-deny (used for non-interactive Telegram)

Per-tool 'always allow' is remembered for the current session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from openbro.core.activity import get_bus


def _pt_escape(text: str) -> str:
    """Minimal HTML-style escape for prompt_toolkit's HTML formatter so
    args containing < > & don't break the markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


YES_PATTERNS = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|haan|han|ha|de|do|kar|kro|allow|approve|theek|sahi|chal)\b",
    re.IGNORECASE,
)
NO_PATTERNS = re.compile(
    r"\b(no|nope|nah|nahi|nai|mat|deny|stop|cancel|chhod|chod|ruk)\b",
    re.IGNORECASE,
)


@dataclass
class PermissionRequest:
    tool: str
    args: dict
    risk: str  # "safe" | "moderate" | "dangerous"
    reason: str = ""


def parse_yes_no(text: str) -> bool | None:
    """Return True/False/None for yes/no/unclear.

    Negation always wins for safety — 'nahi mat kar' contains both 'nahi'
    (no) and 'kar' (do), but the user clearly means NO.
    """
    if not text:
        return None
    t = text.strip()
    yes = bool(YES_PATTERNS.search(t))
    no = bool(NO_PATTERNS.search(t))
    if no:
        return False
    if yes:
        return True
    return None


class PermissionGate:
    def __init__(
        self,
        mode: str = "normal",
        channel: str = "cli",
        voice_listener=None,  # VoiceListener instance for voice mode
        tts=None,  # TextToSpeech instance for voice prompts
    ):
        self.mode = mode
        self.channel = channel
        self.voice_listener = voice_listener
        self.tts = tts
        self._always_allow: set[str] = set()
        self._always_deny: set[str] = set()

    def needs_approval(self, req: PermissionRequest) -> bool:
        if self.mode == "auto":
            return False
        if self.mode == "boss":
            return True
        # normal: only dangerous
        return req.risk == "dangerous"

    def request(self, req: PermissionRequest) -> bool:
        # Session-level memo
        if req.tool in self._always_allow:
            get_bus().emit(
                "permission",
                f"{req.tool}: auto-allow (session)",
                tool=req.tool,
                decision="allow",
                cached=True,
            )
            return True
        if req.tool in self._always_deny:
            get_bus().emit(
                "permission",
                f"{req.tool}: auto-deny (session)",
                tool=req.tool,
                decision="deny",
                cached=True,
            )
            return False

        if not self.needs_approval(req):
            return True

        get_bus().emit(
            "permission",
            f"asking for {req.tool} ({req.risk})",
            tool=req.tool,
            risk=req.risk,
            args=req.args,
        )

        if self.channel == "silent":
            return False
        if self.channel == "voice":
            decision = self._ask_voice(req)
        else:
            decision = self._ask_cli(req)

        get_bus().emit(
            "permission",
            f"{req.tool}: {'ALLOWED' if decision else 'DENIED'}",
            tool=req.tool,
            decision="allow" if decision else "deny",
        )
        return decision

    def _ask_cli(self, req: PermissionRequest) -> bool:
        # Captured 2026-06-20 (TWO failures from the same user):
        #   1. "isme yes no poocha hi nhi???" — text prompt missed.
        #   2. "kai baar yes press kiya but nhi hua aur enter press
        #       krte hi deny ho gya" + "dialog jaisa chat box ke uper
        #       aaye yrr..arrow se select ho jaye?? up dn se".
        # Root cause for #2: Rich's Live spinner that drives the
        # `running elevate · 18.5s` status was sharing the terminal
        # with our blocking console.input — keystrokes got eaten by
        # the redraw loop, and the eventual Enter landed before our
        # input prompt even gained focus, defaulting to NO.
        # Real fix: prompt_toolkit's button_dialog. It takes a hard
        # lock on the terminal, draws a centred modal, and reads the
        # left/right arrow keys + Enter properly. Falls back to the
        # text prompt only when prompt_toolkit isn't installed (CI,
        # minimal envs) or when stdin isn't a TTY (`openbro ask` in
        # scripts, Telegram bot).
        import sys

        prompted = self._try_button_dialog(req) if sys.stdin.isatty() else None
        if prompted is not None:
            return self._record_choice(req, prompted)
        return self._ask_cli_text_fallback(req)

    def _try_button_dialog(self, req: PermissionRequest) -> str | None:
        """Show a real arrow-key modal via prompt_toolkit. Returns the
        chosen action ("yes"/"no"/"always"/"never") or None if the
        widget isn't usable in this environment."""
        try:
            from prompt_toolkit.formatted_text import HTML
            from prompt_toolkit.shortcuts import button_dialog
        except Exception:
            return None

        risk_color = {"safe": "ansigreen", "moderate": "ansiyellow", "dangerous": "ansired"}[
            req.risk
        ]
        args_repr = str(req.args)
        if len(args_repr) > 300:
            args_repr = args_repr[:297] + "…"
        # HTML(...) keeps the modal lightweight + colour-coded without
        # dragging in Rich rendering inside prompt_toolkit's display.
        body_html = (
            f"<b>Tool</b>   {req.tool}\n"
            f"<b>Risk</b>   <{risk_color}>{req.risk}</{risk_color}>\n"
            f"<b>Args</b>   <ansigray>{_pt_escape(args_repr)}</ansigray>"
        )
        if req.reason:
            body_html += f"\n<b>Why</b>    {_pt_escape(req.reason)}"
        try:
            result = button_dialog(
                title=f" Permission required  [{req.risk}] ",
                text=HTML(body_html),
                buttons=[
                    ("Yes", "yes"),
                    ("No", "no"),
                    ("Always allow", "always"),
                    ("Deny always", "never"),
                ],
            ).run()
        except Exception:
            return None
        return result  # None if user pressed Esc

    def _record_choice(self, req: PermissionRequest, choice: str | None) -> bool:
        """Apply the user's selection (incl. session memos)."""
        if choice == "always":
            self._always_allow.add(req.tool)
            return True
        if choice == "never":
            self._always_deny.add(req.tool)
            return False
        return choice == "yes"  # None (Esc) → False, "no" → False

    def _ask_cli_text_fallback(self, req: PermissionRequest) -> bool:
        """Plain-text prompt used in non-TTY environments. Same shape as
        before the prompt_toolkit modal landed."""
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        console = Console()
        risk_color = {"safe": "green", "moderate": "yellow", "dangerous": "red"}[req.risk]
        args_repr = str(req.args)
        if len(args_repr) > 220:
            args_repr = args_repr[:217] + "…"
        body = Text()
        body.append("Tool   ", style="bold")
        body.append(f"{req.tool}\n")
        body.append("Risk   ", style="bold")
        body.append(req.risk, style=f"bold {risk_color}")
        body.append("\n")
        body.append("Args   ", style="bold")
        body.append(args_repr + "\n")
        if req.reason:
            body.append("Why    ", style="bold")
            body.append(f"{req.reason}\n")
        body.append("\n")
        body.append("Allow?", style=f"bold {risk_color}")
        body.append("  (y)es  (n)o  (a)lways allow  (d)eny always")
        console.print(
            Panel(
                body,
                title=f"  Permission required  [{req.risk}]",
                title_align="left",
                border_style=risk_color,
                padding=(1, 2),
            )
        )
        choice = console.input("[bold]› [/bold]").strip().lower()
        if choice in ("a", "always"):
            self._always_allow.add(req.tool)
            return True
        if choice in ("d", "deny always"):
            self._always_deny.add(req.tool)
            return False
        return choice in ("y", "yes")

    def _ask_voice(self, req: PermissionRequest) -> bool:
        question = (
            f"Bhai, {req.tool} tool chalana hai. Risk {req.risk} hai. Permission du? Haan ya nahi?"
        )
        if self.tts:
            self.tts.speak(question)
        # Also print
        from rich.console import Console

        Console().print(f"[yellow]🔊 {question}[/yellow]")

        if not self.voice_listener:
            # No mic - fall back to CLI
            return self._ask_cli(req)

        for _ in range(3):  # 3 retries
            heard = self.voice_listener.listen_once()
            if not heard:
                continue
            decision = parse_yes_no(heard)
            if decision is not None:
                from rich.console import Console

                Console().print(f"[dim]Heard: '{heard}' → {'YES' if decision else 'NO'}[/dim]")
                return decision
            if self.tts:
                self.tts.speak("Samjha nahi. Haan ya nahi bolo.")
        # Give up after retries → deny by default (safety)
        return False
