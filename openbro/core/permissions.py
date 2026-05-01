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
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        risk_color = {"safe": "green", "moderate": "yellow", "dangerous": "red"}[req.risk]
        body = (
            f"[bold]Tool:[/bold] {req.tool}\n"
            f"[bold]Risk:[/bold] [{risk_color}]{req.risk}[/{risk_color}]\n"
            f"[bold]Args:[/bold] {req.args}"
        )
        if req.reason:
            body += f"\n[bold]Why:[/bold] {req.reason}"
        console.print(Panel(body, title="Permission required", border_style=risk_color))
        choice = console.input("[y]es / [n]o / [a]lways allow / [d]eny always > ").strip().lower()
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
