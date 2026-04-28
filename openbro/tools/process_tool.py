"""Process management tool - list, find, and kill processes."""

import platform
import subprocess

from openbro.tools.base import BaseTool, RiskLevel


class ProcessTool(BaseTool):
    name = "process"
    description = "List, search, or kill processes by name or PID"
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
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {query}*"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return result.stdout
            else:
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
