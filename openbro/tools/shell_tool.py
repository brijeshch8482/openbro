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
    description = "Execute shell commands on the system"
    risk = RiskLevel.DANGEROUS

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
