"""Shell command execution tool."""

import subprocess

from openbro.tools.base import BaseTool, RiskLevel

BLOCKED_PATTERNS = [
    "rm -rf /",
    "format c:",
    "del /s /q c:",
    ":(){:|:&};:",
    "mkfs.",
    "> /dev/sda",
]


class ShellTool(BaseTool):
    name = "shell"
    description = (
        "Run a shell / PowerShell / bash command on the user's machine. "
        "Use when no specific tool fits: list files with custom filter, "
        "check disk/process/network state, run a CLI utility. Truly "
        "destructive patterns (rm -rf /, format c:) are blocked. Prefer "
        "the `python` tool for compute/parsing — `shell` is for native "
        "OS commands."
    )
    # Was DANGEROUS, which made the LLM refuse to reach for it in
    # normal queries. Most shell commands a chat agent runs (Get-PSDrive,
    # dir, df, ps, ipconfig) are read-only — the real teeth are the
    # BLOCKED_PATTERNS list above, not the risk tier. Boss mode still
    # gates every moderate tool if the user wants stricter policy.
    risk = RiskLevel.MODERATE

    def run(self, command: str) -> str:
        # Safety check
        cmd_lower = command.lower().strip()
        for blocked in BLOCKED_PATTERNS:
            if blocked in cmd_lower:
                return f"BLOCKED: Dangerous command detected. '{command}' is not allowed."

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=None,
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            if result.returncode != 0:
                output += f"\n(exit code: {result.returncode})"
            return output[:5000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "Command timed out (30s limit)"
        except Exception as e:
            return f"Error: {e}"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                },
                "required": ["command"],
            },
        }
