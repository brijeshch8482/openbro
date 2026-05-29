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

    def run(self, command: str, background: bool = False, timeout: int = 30) -> str:
        # Safety check
        cmd_lower = command.lower().strip()
        for blocked in BLOCKED_PATTERNS:
            if blocked in cmd_lower:
                return f"BLOCKED: Dangerous command detected. '{command}' is not allowed."

        if background:
            return self._run_in_background(command, timeout)
        return self._run_foreground(command, timeout)

    @staticmethod
    def _run_foreground(command: str, timeout: int) -> str:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=None,
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            if result.returncode != 0:
                output += f"\n(exit code: {result.returncode})"
            return output[:5000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return (
                f"Command timed out ({timeout}s limit). Try background=true for long-running jobs."
            )
        except Exception as e:
            return f"Error: {e}"

    def _run_in_background(self, command: str, timeout: int) -> str:
        """Spawn the command as a JobRegistry job, return the job ID
        immediately. The user / agent can poll with the `jobs` REPL
        command or another tool call that checks job status.
        """
        from openbro.core.jobs import JobRegistry

        registry = JobRegistry.get()

        def _runner(job):
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=None,
                )
                out = result.stdout
                if result.stderr:
                    out += f"\nSTDERR: {result.stderr}"
                if result.returncode != 0:
                    out += f"\n(exit code: {result.returncode})"
                return out[:10000] if out else "(no output)"
            except subprocess.TimeoutExpired:
                return f"Background command timed out after {timeout}s."

        job = registry.submit(
            label=f"shell: {command[:60]}",
            fn=_runner,
            meta={"tool": "shell", "command": command, "timeout": timeout},
        )
        return (
            f"Started background job `{job.id}` for command: {command[:60]}.\n"
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
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "background": {
                        "type": "boolean",
                        "description": (
                            "Run the command in a background thread and return a "
                            "job ID immediately. Use for long-running work "
                            "(deep file searches, downloads, scans). User can "
                            "check status via the REPL `jobs` command. Default false."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Timeout in seconds. Foreground default 30s; "
                            "background can use higher values (e.g. 600 for a "
                            "full drive scan). Bounded by sensible upper limit."
                        ),
                    },
                },
                "required": ["command"],
            },
        }
