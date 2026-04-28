"""System information tool."""

import os
import platform

from openbro.tools.base import BaseTool


class SystemTool(BaseTool):
    name = "system_info"
    description = "Get system information like OS, CPU, memory, disk usage"

    def run(self, info_type: str = "all") -> str:
        if info_type == "os":
            return self._os_info()
        elif info_type == "disk":
            return self._disk_info()
        elif info_type == "env":
            return self._env_info()
        elif info_type == "all":
            parts = [self._os_info(), self._disk_info()]
            return "\n\n".join(parts)
        else:
            return f"Unknown info type: {info_type}. Available: os, disk, env, all"

    def _os_info(self) -> str:
        return (
            f"OS: {platform.system()} {platform.release()}\n"
            f"Version: {platform.version()}\n"
            f"Machine: {platform.machine()}\n"
            f"Processor: {platform.processor()}\n"
            f"Python: {platform.python_version()}\n"
            f"Hostname: {platform.node()}"
        )

    def _disk_info(self) -> str:
        try:
            import shutil
            total, used, free = shutil.disk_usage("/")
            return (
                f"Disk Usage:\n"
                f"  Total: {total // (1024**3)} GB\n"
                f"  Used:  {used // (1024**3)} GB\n"
                f"  Free:  {free // (1024**3)} GB"
            )
        except Exception:
            return "Disk info unavailable"

    def _env_info(self) -> str:
        safe_vars = ["PATH", "HOME", "USER", "SHELL", "LANG", "TERM"]
        lines = []
        for var in safe_vars:
            val = os.environ.get(var, "not set")
            lines.append(f"  {var}: {val}")
        return "Environment:\n" + "\n".join(lines)

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "info_type": {
                        "type": "string",
                        "enum": ["os", "disk", "env", "all"],
                        "description": "Type of system info to retrieve",
                    },
                },
                "required": [],
            },
        }
