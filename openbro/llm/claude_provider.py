"""LLMProvider that uses the Claude Code CLI as the chat backend.

Lets the user set `llm.provider = claude` and have every turn run
through the locally-installed `claude` binary. The CLI is already
signed in with the user's Anthropic / Claude.ai subscription, so this
gives Claude-Sonnet-level coding without an ANTHROPIC_API_KEY and
without paying per-token.

Trade-off vs the direct anthropic_provider:
  * Pros — uses the user's existing Claude subscription, no API key,
    no per-token billing, and `claude` is Anthropic's own CLI so it's
    less likely to break from API drift than a hand-rolled wrapper.
  * Cons — spawns a fresh process per turn so cold-start latency is
    higher than the API (~3-8 s). For high-volume scripted use the
    AnthropicProvider with an API key is faster.

Tool calling: `claude --print` returns plain text. Claude's internal
tool use is opaque to us — we treat the whole reply as the chat
content. The agent loop's <tool_call> text parser still works for
OpenBro tools the model decides to invoke.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time

from openbro.llm.base import LLMProvider, LLMResponse, Message


def _get_activity_bus():
    """Lazy import of the activity bus so this module stays usable
    even from short scripts that don't pull in the agent loop."""
    try:
        from openbro.core.activity import get_bus

        return get_bus()
    except Exception:
        return None


class ClaudeProvider(LLMProvider):
    """Shell out to `claude --print` for every chat turn."""

    # Optional `(cost ...)` / `(input/output tokens)` footer that some
    # versions of `claude --print` print after the answer. Strip it so
    # the user sees just the response. Captured 2026-06-20 against
    # claude 2.1.183: bare `--print` returns ONLY the answer, no
    # footer, but older builds added a trailing summary line.
    _FOOTER_RE = re.compile(r"\n\(\s*(?:cost|tokens|usage)\b[^)]*\)\s*$", re.IGNORECASE | re.DOTALL)

    def __init__(
        self,
        binary: str = "claude",
        timeout: int = 300,
    ) -> None:
        self._binary = binary
        self._timeout = timeout

    # ─── LLMProvider interface ────────────────────────────────────

    def name(self) -> str:
        return "claude/code-cli"

    def supports_tools(self) -> bool:
        # Claude's tool use stays inside the CLI's own loop; we only
        # see the final text. OpenBro's agent loop already understands
        # text-form <tool_call> blocks, so the existing chat path
        # keeps working.
        return False

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        # Resolve to the full path. On Windows the npm-installed
        # `claude` is a .cmd shim that subprocess can't locate from a
        # bare name (CreateProcess returns WinError 2). which() finds
        # it on PATH — same fix as CodexProvider.
        resolved = shutil.which(self._binary)
        if resolved is None:
            raise RuntimeError(
                f"`{self._binary}` not on PATH. Install Claude Code CLI:\n"
                "  npm install -g @anthropic-ai/claude-code\n"
                "Then sign in once: claude login"
            )
        prompt = self._flatten(messages)
        # Captured 2026-06-20: claude --print with OpenBro's big
        # system prompt (~30 KB) takes ~20 s end-to-end on the
        # subscription tier. subprocess.run() blocked the main
        # thread and the spinner froze; the user thought OpenBro
        # was stuck and said so twice ("ye stuck ho jata hai…",
        # "jabaab hi nhi de rha"). Real fix: keep the call but
        # spawn a heartbeat thread that emits a "thinking" event
        # every couple of seconds with elapsed time. The activity
        # bus drives the REPL spinner, so as long as it sees
        # events it keeps rendering "20s elapsed, still working".
        try:
            proc = subprocess.Popen(
                [resolved, "-p", "--output-format", "text"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"Could not spawn {resolved}: {e}") from e

        try:
            proc.stdin.write(prompt)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

        bus = _get_activity_bus()
        started = time.time()
        stop_flag = threading.Event()

        def _heartbeat() -> None:
            # 2-second cadence balances "spinner is alive" against
            # event-bus noise. The first beat goes out after 1 s so
            # the user sees activity almost immediately even on
            # fast turns.
            time.sleep(1.0)
            while not stop_flag.is_set():
                if bus is not None:
                    elapsed = time.time() - started
                    bus.emit(
                        "thinking",
                        f"claude · {elapsed:.0f}s",
                        source="claude",
                        elapsed=elapsed,
                    )
                stop_flag.wait(2.0)

        beater = threading.Thread(target=_heartbeat, daemon=True)
        beater.start()

        try:
            try:
                stdout_text, stderr_text = proc.communicate(timeout=self._timeout)
            except subprocess.TimeoutExpired as e:
                proc.kill()
                raise RuntimeError(f"Claude CLI timed out after {self._timeout}s") from e
        finally:
            stop_flag.set()
            beater.join(timeout=0.5)

        if proc.returncode not in (0, None):
            raise RuntimeError(
                f"Claude exited {proc.returncode}: {(stderr_text or '').strip()[:500]}"
            )

        answer = self._extract_answer(stdout_text or "")
        return LLMResponse(
            content=answer,
            tool_calls=[],
            usage={},
            model="claude-via-cli",
        )

    # ─── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _flatten(messages: list[Message]) -> str:
        """Claude --print takes a single prompt string. Roll the
        conversation into a Markdown-ish transcript so the model sees
        the history (same shape as CodexProvider)."""
        parts: list[str] = []
        for m in messages:
            role = (m.role or "user").lower()
            content = m.content or ""
            if not content:
                continue
            if role == "system":
                parts.append(f"## System\n{content}")
            elif role == "user":
                parts.append(f"## User\n{content}")
            elif role == "assistant":
                parts.append(f"## Assistant\n{content}")
            elif role == "tool":
                parts.append(f"## Tool output\n{content}")
        return "\n\n".join(parts).strip()

    @classmethod
    def _extract_answer(cls, stdout: str) -> str:
        return cls._FOOTER_RE.sub("", stdout).strip()
