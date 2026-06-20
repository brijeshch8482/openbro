"""Run a command with admin / UAC elevation on Windows.

The plain `shell` tool runs as the current user. Windows blocks a
lot of routine cleanup (clearing C:\\Windows\\Temp, removing
locked Recycle Bin entries, touching system services) for non-
admin processes, and OpenBro currently swallows the resulting
"Access denied" errors without ever asking the user whether to
retry elevated.

This tool fills the gap. When the LLM sees a permission-denied
failure it can re-issue the same command through `elevate`, which
spawns the command via PowerShell's `Start-Process -Verb RunAs`.
Windows pops the UAC consent dialog; once the user clicks Yes, the
command runs as administrator and stdout/stderr are routed back.

Same BLOCKED_PATTERNS list as the shell tool — the user can't
turn this into a kill switch by typing `format c:`. UAC adds a
second confirmation layer too.
"""

from __future__ import annotations

import platform
import subprocess
import tempfile
import time
from pathlib import Path

from openbro.tools.base import BaseTool, RiskLevel
from openbro.tools.shell_tool import BLOCKED_PATTERNS


class ElevateTool(BaseTool):
    name = "elevate"
    description = (
        "Run a command with administrator privileges on Windows (UAC). "
        "Use ONLY when the regular `shell` tool fails with 'Access denied' "
        "or 'requires elevation' style errors — clearing C:\\Windows\\Temp, "
        "modifying HKLM registry, stopping system services, etc. The user "
        "will see a Windows UAC prompt and must consent before the command "
        "runs. Truly destructive patterns (rm -rf /, format c:) are blocked "
        "the same as in the regular shell tool."
    )
    risk = RiskLevel.DANGEROUS

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The shell/PowerShell command to run as admin. "
                            "Example: 'Remove-Item -Recurse -Force "
                            "C:\\\\Windows\\\\Temp\\\\*'"
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Short Hinglish/English explanation of WHY "
                            "admin is needed (shown to user in UAC "
                            "request). E.g. 'C:/Windows/Temp clean karne "
                            "ke liye admin chahiye'."
                        ),
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Max wait time. Defaults to 120.",
                        "default": 120,
                    },
                },
                "required": ["command", "reason"],
            },
        }

    def run(self, command: str, reason: str = "", timeout_seconds: int = 120) -> str:
        if platform.system() != "Windows":
            return (
                "elevate is Windows-only — on Linux/macOS, ask the user to "
                "re-run OpenBro with sudo instead."
            )
        for bad in BLOCKED_PATTERNS:
            if bad.lower() in command.lower():
                return f"Refused — command matches blocked pattern: {bad!r}"

        # Write the command + its output capture to a one-off PowerShell
        # script. We need a separate file because Start-Process can't
        # carry stdout back unless we redirect inside the elevated
        # process itself. Once the elevated shell exits we read the
        # captured stdout/stderr and clean up.
        with tempfile.TemporaryDirectory(prefix="openbro_elevate_") as tmpdir:
            tmp = Path(tmpdir)
            script_path = tmp / "cmd.ps1"
            stdout_path = tmp / "out.txt"
            stderr_path = tmp / "err.txt"
            done_path = tmp / "done.flag"

            # PowerShell here-string with the user's command. Surrounding
            # try/finally guarantees the `done` flag flips even on
            # error, so the parent doesn't wait forever.
            script = (
                "$ErrorActionPreference = 'Continue'\n"
                "try {\n"
                f"  & {{ {command} }} *> '{stdout_path}' 2> '{stderr_path}'\n"
                "} catch {\n"
                f"  $_ | Out-File '{stderr_path}' -Append\n"
                "} finally {\n"
                f"  Set-Content '{done_path}' 'done'\n"
                "}\n"
            )
            script_path.write_text(script, encoding="utf-8")

            # Launch elevated PowerShell that runs the script and exits.
            launcher = (
                "Start-Process -FilePath powershell -Verb RunAs -WindowStyle "
                "Hidden -ArgumentList "
                f"'-NoProfile','-ExecutionPolicy','Bypass','-File','{script_path}'"
            )
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", launcher],
                    check=False,
                    timeout=15,  # just enough to spawn the UAC dialog
                )
            except subprocess.TimeoutExpired:
                return "Timeout starting elevated PowerShell — UAC prompt may have hung."

            # Poll for the done flag. UAC consent + the command itself
            # share the timeout budget.
            deadline = time.time() + max(10, timeout_seconds)
            while time.time() < deadline:
                if done_path.exists():
                    break
                time.sleep(0.5)
            else:
                return (
                    f"Elevated command did not finish within "
                    f"{timeout_seconds}s. UAC may have been denied."
                )

            out = (
                stdout_path.read_text(encoding="utf-8", errors="replace")
                if stdout_path.exists()
                else ""
            )
            err = (
                stderr_path.read_text(encoding="utf-8", errors="replace")
                if stderr_path.exists()
                else ""
            )
            tail_out = out.strip()[-2000:] if out else ""
            tail_err = err.strip()[-1000:] if err else ""
            if tail_err and not tail_out:
                return f"Elevated command produced no stdout. stderr:\n{tail_err}"
            if tail_err:
                return f"Elevated command output:\n{tail_out}\n\n(stderr also):\n{tail_err}"
            return (
                f"Elevated command completed:\n{tail_out}"
                if tail_out
                else "Elevated command completed (no output)."
            )
