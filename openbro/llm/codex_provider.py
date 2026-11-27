"""LLMProvider that uses OpenAI's Codex CLI as the chat backend.

Lets the user point `llm.provider = codex` and have every turn run
through the locally-installed `codex` binary, which is already
signed in to their ChatGPT account. No API key needed — Codex
handles the OAuth + subscription billing itself.

Trade-off vs the API providers:
  * Pros — uses the user's existing ChatGPT Plus subscription, no
    OPENAI_API_KEY required, no per-token rate limiting at this layer.
  * Cons — Codex doesn't expose native function calling, so this
    provider reports supports_tools()=False. The agent loop already
    knows how to parse text-emitted <tool_call> blocks, so that path
    still works.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time

from openbro.core.tool_protocol import render_transcript
from openbro.llm.base import LLMProvider, LLMResponse, Message


def _get_activity_bus():
    """Lazy import the activity bus so this provider stays importable
    from minimal scripts that don't pull the agent loop."""
    try:
        from openbro.core.activity import get_bus

        return get_bus()
    except Exception:
        return None


# Codex's own built-in tools. They are switched off so Codex answers as the
# model only and acts through OpenBro's tools: anything Codex did by itself
# (run a shell, generate an image into ~/.codex/generated_images) would skip
# OpenBro's permission gate, and OpenBro could not tell the user what was
# made or where.
_CODEX_BUILTIN_FEATURES = (
    "shell_tool",
    "image_generation",
    "view_image",
    "apps",
    "plugins",
    "browser_use",
    "in_app_browser",
    "computer_use",
    "skill_search",
    "tool_suggest",
)
# Codex is a full agent with tools of its own. Inside OpenBro it is only the
# model: it runs read-only, and is told to act through OpenBro's tools.
_ROLE_NOTE = (
    "[Your role] You are the language model inside OpenBro, not a coding agent. "
    "Do not use your own built-in tools (exec, shell, apply_patch, image "
    "generation, web search, sub-agents): they are switched off or read-only here, "
    "and nothing they do reaches the user. To act, reply with <tool_call> tags "
    "exactly as the OpenBro instructions below describe; OpenBro runs each call "
    "and sends you the result.\n\n"
)
_UNKNOWN_FEATURE_RE = re.compile(r"Unknown feature flag:\s*([\w.-]+)")


class CodexProvider(LLMProvider):
    """Shell out to `codex exec` for every chat turn."""

    # Feature names this Codex version doesn't know (older or newer CLI);
    # dropped from later calls instead of failing every turn.
    _unknown_features: set[str] = set()

    # Patterns to strip from Codex stdout so the user only sees the
    # actual answer. Captured 2026-06-16 against codex 0.130.0:
    #   session id: <uuid>
    #   --------
    #   user
    #   <prompt>
    #   <model ERROR ...>
    #   codex
    #   <answer>
    #   tokens used
    #   <n>
    _NOISE = (
        re.compile(r"^session id: .*$", re.MULTILINE),
        re.compile(r"^-+$", re.MULTILINE),
        re.compile(r"^\d{4}-\d{2}-\d{2}T.*$", re.MULTILINE),  # timestamps
        re.compile(r"^tokens used\s*$", re.MULTILINE),
    )
    _ANSWER_RE = re.compile(r"\bcodex\b\s*\n(.*?)(?=\btokens used\b|\Z)", re.DOTALL)

    def __init__(
        self,
        binary: str = "codex",
        timeout: int = 180,
        max_output_kb: int = 256,
    ) -> None:
        self._binary = binary
        self._timeout = timeout
        self._max_output_kb = max_output_kb

    # ─── LLMProvider interface ────────────────────────────────────

    def name(self) -> str:
        return "codex/chatgpt"

    def supports_tools(self) -> bool:
        # Codex parses tool calls itself; OpenBro's agent loop also
        # accepts text-form <tool_call> blocks. Native function-
        # calling API isn't exposed, so flag this as False to keep
        # the chat path simple.
        return False

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        # Resolve to the full path. On Windows the npm-installed
        # `codex` is a .cmd shim that subprocess can't locate from a
        # bare name (CreateProcess returns WinError 2). which() finds
        # it on PATH.
        resolved = shutil.which(self._binary)
        if resolved is None:
            raise RuntimeError(
                f"`{self._binary}` not on PATH. Install Codex CLI:\n"
                "  npm install -g @openai/codex\n"
                "Then sign in once: codex login"
            )
        prompt = self._flatten(messages)
        # Pipe via stdin instead of arg — OpenBro's flattened prompt
        # (system + tools + history) routinely exceeds Windows' 8 KB
        # command-line cap and the CLI rejects it with "The command
        # line is too long." Captured 2026-06-16 in REPL with a
        # 30+ tool system prompt. `codex exec -` (or piped stdin with
        # no prompt arg) reads instructions from stdin.
        # Heartbeat thread keeps the REPL spinner alive while codex
        # works — long turns can easily hit 30 s. Same fix as
        # ClaudeProvider; captured 2026-06-20 user complaint about
        # the agent appearing stuck.
        bus = _get_activity_bus()
        started = time.time()
        stop_flag = threading.Event()

        def _heartbeat() -> None:
            time.sleep(1.0)
            while not stop_flag.is_set():
                if bus is not None:
                    elapsed = time.time() - started
                    bus.emit(
                        "thinking",
                        f"codex · {elapsed:.0f}s",
                        source="codex",
                        elapsed=elapsed,
                    )
                stop_flag.wait(2.0)

        beater = threading.Thread(target=_heartbeat, daemon=True)
        beater.start()
        try:
            while True:
                returncode, stdout_text, stderr_text = self._run(resolved, prompt)
                unknown = _UNKNOWN_FEATURE_RE.search(stderr_text or "")
                if returncode in (0, None) or not unknown:
                    break
                if unknown.group(1) in self._unknown_features:
                    break  # dropping it didn't help; report the error
                self._unknown_features.add(unknown.group(1))
        finally:
            stop_flag.set()
            beater.join(timeout=0.5)

        if returncode not in (0, None):
            raise RuntimeError(f"Codex exited {returncode}: {(stderr_text or '').strip()[:500]}")
        answer = self._extract_answer(stdout_text or "")
        # Codex prints "tokens used\n<n>" at the end; pull the count out
        # so the REPL status line keeps working even without an API
        # usage object.
        # (newer CLIs print it on stderr, older ones on stdout)
        usage = self._extract_usage(stderr_text or "") or self._extract_usage(stdout_text or "")
        return LLMResponse(
            content=answer,
            tool_calls=[],
            usage=usage,
            model="chatgpt-via-codex",
        )

    def _command(self, resolved: str) -> list[str]:
        # --skip-git-repo-check lets Codex run from any folder.
        # --sandbox read-only plus the disabled features keep Codex to
        # answering: every action goes through OpenBro's tools instead.
        cmd = [resolved, "exec", "--skip-git-repo-check", "--sandbox", "read-only"]
        cmd += ["-c", 'web_search="disabled"']
        for feature in _CODEX_BUILTIN_FEATURES:
            if feature not in self._unknown_features:
                cmd += ["--disable", feature]
        cmd.append("-")  # read the prompt from stdin
        return cmd

    def _run(self, resolved: str, prompt: str) -> tuple[int | None, str, str]:
        # The prompt goes in on stdin: OpenBro's prompt (system + tools +
        # history) is far over Windows' 8 KB command-line limit.
        try:
            proc = subprocess.Popen(
                self._command(resolved),
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
            stdout_text, stderr_text = proc.communicate(prompt, timeout=self._timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            raise RuntimeError(f"Codex CLI timed out after {self._timeout}s") from e
        return proc.returncode, stdout_text or "", stderr_text or ""

    # ─── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _flatten(messages: list[Message]) -> str:
        """Codex takes a single prompt string. Roll the conversation
        into a transcript; earlier tool calls are written back as
        <tool_call> tags and tool output as <tool_result> blocks so the
        model sees the same text protocol it is asked to use."""
        return _ROLE_NOTE + render_transcript(messages)

    @classmethod
    def _extract_answer(cls, stdout: str) -> str:
        m = cls._ANSWER_RE.search(stdout)
        if m:
            text = m.group(1)
        else:
            text = stdout
        for pat in cls._NOISE:
            text = pat.sub("", text)
        return text.strip()

    @staticmethod
    def _extract_usage(stdout: str) -> dict:
        m = re.search(r"tokens used\s*\n\s*([\d,]+)", stdout)
        if not m:
            return {}
        n = int(m.group(1).replace(",", ""))
        # Codex reports a single combined number; surface it as output
        # so the status bar shows something meaningful.
        return {"output": n, "input": 0, "total": n}
