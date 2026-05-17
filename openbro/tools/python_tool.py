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

    def run(self, code: str) -> str:
        if not isinstance(code, str) or not code.strip():
            return "Error: code must be a non-empty string"

        code_low = code.lower().replace(" ", "")
        for blocked in BLOCKED_PATTERNS:
            if blocked.lower().replace(" ", "") in code_low:
                return f"BLOCKED: snippet contains '{blocked}'"

        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            output = result.stdout or ""
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n(exit {result.returncode})"
            return (output or "(no output)")[:5000]
        except subprocess.TimeoutExpired:
            return "Python snippet timed out (30s limit)"
        except OSError as e:
            return f"Python execution error: {e}"

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
                    }
                },
                "required": ["code"],
            },
        }
