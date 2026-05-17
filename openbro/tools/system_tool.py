"""System information tool."""

import os
import platform

from openbro.tools.base import BaseTool, RiskLevel


class SystemTool(BaseTool):
    name = "system_info"
    description = "Get system information like OS, CPU, memory, disk usage"
    risk = RiskLevel.SAFE

    def run(self, info_type: str = "all", drive: str | None = None) -> str:
        if info_type == "os":
            return self._os_info()
        elif info_type == "disk":
            return self._disk_info(drive=drive)
        elif info_type == "env":
            return self._env_info()
        elif info_type == "all":
            parts = [self._os_info(), self._disk_info(drive=drive)]
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

    def _disk_info(self, drive: str | None = None) -> str:
        """Report disk space.

        Defaults to enumerating EVERY drive on Windows (was: only '/', which
        resolves to the current working drive — so a user sitting in D:\\
        and asking 'C drive me kitna space hai' got D's numbers misreported
        as C). On Linux/macOS we still report root '/' unless a path is
        explicitly passed.
        """
        import shutil

        results: list[tuple[str, int, int, int]] = []

        if drive:
            try:
                t, u, f = shutil.disk_usage(drive)
                results.append((drive, t, u, f))
            except OSError:
                return f"Drive {drive} not accessible"
        elif platform.system() == "Windows":
            import string

            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                try:
                    t, u, f = shutil.disk_usage(root)
                    results.append((root, t, u, f))
                except OSError:
                    # Drive letter not present — skip silently
                    continue
        else:
            try:
                t, u, f = shutil.disk_usage("/")
                results.append(("/", t, u, f))
            except OSError:
                return "Disk info unavailable"

        if not results:
            return "Disk info unavailable (no drives detected)"

        lines = ["Disk Usage:"]
        for path, total, used, free in results:
            total_gb = total // (1024**3)
            used_gb = used // (1024**3)
            free_gb = free // (1024**3)
            pct = round((used / total) * 100, 1) if total else 0
            lines.append(
                f"  {path}  total={total_gb} GB  used={used_gb} GB ({pct}%)  free={free_gb} GB"
            )
        return "\n".join(lines)

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
                    "drive": {
                        "type": "string",
                        "description": (
                            "Optional specific drive/path for disk info "
                            "(e.g. 'C:\\\\' on Windows, '/home' on Linux). "
                            "If omitted, every drive is reported."
                        ),
                    },
                },
                "required": [],
            },
        }
