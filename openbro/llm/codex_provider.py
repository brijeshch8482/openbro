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

from openbro.llm.base import LLMProvider, LLMResponse, Message


class CodexProvider(LLMProvider):
    """Shell out to `codex exec` for every chat turn."""

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

    def chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> LLMResponse:
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
        try:
            # --skip-git-repo-check lets Codex run from any cwd; without
            # it the CLI refuses with "Not inside a trusted directory"
            # whenever OpenBro is invoked from a non-git folder (e.g.
            # D:/OpenBro-teting/). The user already signed in, so the
            # safety gate is unnecessary at this layer — OpenBro's own
            # permission system gates write actions anyway.
            proc = subprocess.run(
                [resolved, "exec", "--skip-git-repo-check", "-"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Codex CLI timed out after {self._timeout}s"
            ) from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"Codex exited {proc.returncode}: {proc.stderr.strip()[:500]}"
            )
        answer = self._extract_answer(proc.stdout)
        # Codex prints "tokens used\n<n>" at the end; pull the count out
        # so the REPL status line keeps working even without an API
        # usage object.
        usage = self._extract_usage(proc.stdout)
        return LLMResponse(
            content=answer,
            tool_calls=[],
            usage=usage,
            model="chatgpt-via-codex",
        )

    # ─── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _flatten(messages: list[Message]) -> str:
        """Codex takes a single prompt string. Roll the conversation
        into a Markdown-ish transcript so the model sees the history."""
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
        # The last user message becomes the leading instruction; the
        # earlier context follows so codex sees it as background.
        return "\n\n".join(parts).strip()

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
