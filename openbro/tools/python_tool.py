"""Python execution tool — the agent's brain-extension.

Why this exists: every time a user asks something OpenBro's narrow tools
don't cover ('count text files in OneDrive Desktop', 'how much RAM is
free', 'parse this JSON'), the agent used to give up or report wrong
data from a half-fitting tool (e.g. system_info reporting D's stats as
C's). Real intelligence = write a short Python snippet, run it, return
the answer. That's how Claude Code works.

Risk: MODERATE. Python can do destructive things (delete files, hit
the network) but stdout is captured and the call is sandboxed via a
subprocess with a 30s timeout. The blocklist catches the obvious
horrors; the permission gate's boss mode can require confirmation
for every call if the user wants stricter policy.

Examples the LLM can write:
    import shutil; print(shutil.disk_usage('C:\\\\'))
    from pathlib import Path; print(len(list(Path.home().glob('*.txt'))))
    import json, httpx; print(httpx.get('https://api.github.com').json())
"""

from __future__ import annotations

import subprocess
import sys

from openbro.tools.base import BaseTool, RiskLevel

# Substrings that the runner refuses outright. This is a "don't shoot yourself
# in the foot" guard, not a security boundary — real protection comes from
# the permission_gate on every tool call.
BLOCKED_PATTERNS = [
    "rm -rf /",
    "shutil.rmtree('/')",
    'shutil.rmtree("/")',
    "format c:",
    "del /s /q c:",
    "os.system('format",
    'os.system("format',
]


class PythonTool(BaseTool):
    name = "python"
    description = (
        "Run a short Python snippet to compute, parse, or look something up. "
        "Use this whenever no narrow tool fits — write the smallest correct "
        "code, run it, return the result. Stdlib + httpx/numpy available. "
        "Use print() for output. Times out at 30s; output capped at 5 KB. "
        "Examples: list files in a folder with a specific extension, parse "
        "JSON, compute disk usage of a specific drive, count items, do math."
    )
    risk = RiskLevel.MODERATE

    def run(self, code: str, background: bool = False, timeout: int = 30) -> str:
        if not isinstance(code, str) or not code.strip():
            return "Error: code must be a non-empty string"

        code_low = code.lower().replace(" ", "")
        for blocked in BLOCKED_PATTERNS:
            if blocked.lower().replace(" ", "") in code_low:
                return f"BLOCKED: snippet contains '{blocked}'"

        if background:
            return self._run_in_background(code, timeout)
        return self._run_foreground(code, timeout)

    @staticmethod
    def _run_foreground(code: str, timeout: int) -> str:
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            # Distinguish:
            #   exit_code != 0  → real failure, force a retry
            #   exit_code == 0 with stderr → warning (openpyxl,
            #     deprecation, etc.) — script succeeded, surface
            #     stdout normally but include stderr as a note.
            # Captured 2026-05-31: script returned exit 0 with a JSON
            # result on stdout and an openpyxl UserWarning on stderr;
            # the old code labelled the WHOLE thing 'ERROR — snippet
            # did NOT produce a usable answer', which made the model
            # discard a perfectly good result.
            if result.returncode != 0:
                return (
                    "ERROR — snippet exited non-zero. Fix the code "
                    "and retry; DO NOT report a result yet.\n"
                    f"exit_code: {result.returncode}\n"
                    f"stdout: {stdout.strip() or '(empty)'}\n"
                    f"stderr: {stderr.strip()[:1500]}"
                )[:5000]
            # exit_code == 0 → success. Surface stdout as the result;
            # include stderr as a quiet warning footer only if it has
            # genuine content (not just whitespace).
            out = stdout or "(no output)"
            if stderr.strip():
                out = f"{out.rstrip()}\n\n[stderr warning, exit 0]\n{stderr.strip()[:600]}"
            return out[:5000]
        except subprocess.TimeoutExpired:
            return (
                f"Python snippet timed out ({timeout}s limit). "
                f"Try background=true for long-running scripts."
            )
        except OSError as e:
            return f"Python execution error: {e}"

    def _run_in_background(self, code: str, timeout: int) -> str:
        from openbro.core.jobs import JobRegistry

        registry = JobRegistry.get()

        def _runner(job):
            try:
                result = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    encoding="utf-8",
                    errors="replace",
                )
                stdout = result.stdout or ""
                stderr = result.stderr or ""
                if result.returncode != 0:
                    return (
                        "ERROR — snippet exited non-zero.\n"
                        f"exit_code: {result.returncode}\n"
                        f"stdout: {stdout.strip() or '(empty)'}\n"
                        f"stderr: {stderr.strip()[:1500]}"
                    )[:10000]
                out = stdout or "(no output)"
                if stderr.strip():
                    out = f"{out.rstrip()}\n\n[stderr warning, exit 0]\n{stderr.strip()[:600]}"
                return out[:10000]
            except subprocess.TimeoutExpired:
                return f"Background snippet timed out after {timeout}s."

        first_line = code.strip().splitlines()[0][:60]
        job = registry.submit(
            label=f"python: {first_line}",
            fn=_runner,
            meta={"tool": "python", "timeout": timeout},
        )
        return (
            f"Started background job `{job.id}` (python).\n"
            f"Check status with the REPL `jobs` command, or pass "
            f"`background=false` to wait inline."
        )

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python source to execute. Use print() for output. "
                            "stdlib is available; httpx/numpy if installed. "
                            "Keep it small — one short script that answers "
                            "the question."
                        ),
                    },
                    "background": {
                        "type": "boolean",
                        "description": (
                            "Run in a background thread, return job ID immediately. "
                            "Use for long-running scripts (deep walks, downloads). "
                            "User can check status with REPL `jobs` command. "
                            "Default false."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Timeout in seconds. Foreground default 30s. "
                            "Background can use higher values (e.g. 300)."
                        ),
                    },
                },
                "required": ["code"],
            },
        }
