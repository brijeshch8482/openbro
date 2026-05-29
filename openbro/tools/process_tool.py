"""Process management tool - list, find, and kill processes."""

import json
import platform
import subprocess

from openbro.tools.base import BaseTool, RiskLevel


class ProcessTool(BaseTool):
    name = "process"
    description = (
        "List, search, or kill processes by name OR command line. "
        "On Windows, 'find' matches BOTH the exe name AND the full command "
        "line — so searching 'claude' finds Claude Code running inside "
        "node.exe, 'openbro' finds it inside python.exe, etc. Don't tell the "
        "user 'process not found' until you've also tried adjacent names "
        "(claude -> node, code -> Code.exe / electron, jupyter -> python)."
    )
    risk = RiskLevel.MODERATE

    def run(self, action: str, query: str = "", pid: int = 0) -> str:
        if action == "list":
            return self._list(limit=30)
        elif action == "find":
            return self._find(query)
        elif action == "kill":
            return self._kill(query, pid)
        else:
            return f"Unknown action: {action}. Available: list, find, kill"

    def _list(self, limit: int = 30) -> str:
        system = platform.system()
        try:
            if system == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FO", "TABLE"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                lines = result.stdout.strip().split("\n")[: limit + 3]
                return "\n".join(lines)
            else:
                result = subprocess.run(
                    ["ps", "aux", "--sort=-%cpu"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                lines = result.stdout.strip().split("\n")[: limit + 1]
                return "\n".join(lines)
        except Exception as e:
            return f"Error listing processes: {e}"

    def _find(self, query: str) -> str:
        if not query:
            return "Query required for find"
        system = platform.system()
        try:
            if system == "Windows":
                return self._find_windows(query)
            # *nix: pgrep -f already matches against the full command line,
            # so 'claude' finds Node-hosted Claude Code, etc.
            result = subprocess.run(
                ["pgrep", "-laf", query],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if not result.stdout.strip():
                return f"No processes matching: {query}"
            return result.stdout
        except Exception as e:
            return f"Error finding processes: {e}"

    @staticmethod
    def _find_windows(query: str) -> str:
        """Search both Name AND CommandLine via Get-CimInstance.

        Previous version used `tasklist /FI "IMAGENAME eq <q>*"` which only
        matches the exe filename. Captured failure: user asked 'is Claude
        running' — Claude Code runs inside `node.exe` with 'claude' in the
        command line, so the old find returned 'no processes matching'
        even though it was right there. New impl uses WMI/CIM which exposes
        the full CommandLine, so substring matches against either.
        """
        # Query passed via env var so user-supplied text never enters the
        # PowerShell script source (no string injection / quote-breakouts).
        ps_script = (
            "$q = $env:OPENBRO_PROC_QUERY; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "  ($_.Name -and $_.Name -match [regex]::Escape($q)) -or "
            "  ($_.CommandLine -and $_.CommandLine -match [regex]::Escape($q)) "
            "} | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Depth 2"
        )
        env = {"OPENBRO_PROC_QUERY": query}
        import os as _os

        env = {**_os.environ, **env}
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        out = (result.stdout or "").strip()
        if not out:
            return (
                f"No processes matching '{query}' (checked both exe name "
                "AND command line). Try a synonym (e.g. 'claude' might be "
                "running as 'node' with the CLI in args)."
            )
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            # PowerShell sometimes emits raw lines if ConvertTo-Json fails
            return out[:4000]
        if isinstance(data, dict):
            data = [data]
        if not data:
            return f"No processes matching '{query}'."

        lines = [f"Found {len(data)} process(es) matching '{query}':"]
        for item in data[:20]:
            pid = item.get("ProcessId")
            name = item.get("Name") or "?"
            cmd = (item.get("CommandLine") or "").strip()
            if len(cmd) > 200:
                cmd = cmd[:200] + " …"
            lines.append(f"  PID {pid}  {name}  |  {cmd}" if cmd else f"  PID {pid}  {name}")
        if len(data) > 20:
            lines.append(f"  ... (+{len(data) - 20} more)")
        return "\n".join(lines)

    def _kill(self, query: str, pid: int) -> str:
        system = platform.system()
        try:
            if pid:
                if system == "Windows":
                    result = subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                else:
                    result = subprocess.run(
                        ["kill", "-9", str(pid)],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                if result.returncode == 0:
                    return f"Killed PID {pid}"
                return f"Could not kill PID {pid}: {result.stderr.strip()}"

            if not query:
                return "Either pid or query (process name) required for kill"

            if system == "Windows":
                exe = query if query.endswith(".exe") else query + ".exe"
                result = subprocess.run(
                    ["taskkill", "/F", "/IM", exe],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            else:
                result = subprocess.run(
                    ["pkill", "-f", query],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            if result.returncode == 0:
                return f"Killed processes matching: {query}"
            return f"Kill result: {result.stderr.strip() or 'No matching process'}"

        except Exception as e:
            return f"Error killing process: {e}"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "find", "kill"],
                        "description": "list all processes, find by name, or kill",
                    },
                    "query": {
                        "type": "string",
                        "description": "Process name (for find/kill)",
                    },
                    "pid": {
                        "type": "integer",
                        "description": "Process ID (for kill by PID)",
                    },
                },
                "required": ["action"],
            },
        }
