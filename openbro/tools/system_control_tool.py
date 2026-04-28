"""System control tool - lock, sleep, shutdown, volume, brightness."""

import platform
import subprocess

from openbro.tools.base import BaseTool, RiskLevel


class SystemControlTool(BaseTool):
    name = "system_control"
    description = (
        "Control the system: lock screen, sleep, shutdown, restart, set volume, mute/unmute"
    )
    risk = RiskLevel.DANGEROUS

    def run(self, action: str, value: int = 0) -> str:
        system = platform.system()
        action = action.lower().strip()

        try:
            if action == "lock":
                return self._lock(system)
            elif action == "sleep":
                return self._sleep(system)
            elif action in ("shutdown", "poweroff"):
                return self._shutdown(system)
            elif action == "restart":
                return self._restart(system)
            elif action == "mute":
                return self._mute(system)
            elif action == "unmute":
                return self._unmute(system)
            elif action == "volume":
                return self._volume(system, value)
            else:
                return (
                    f"Unknown action: {action}. Available: "
                    "lock, sleep, shutdown, restart, mute, unmute, volume"
                )
        except Exception as e:
            return f"System control failed: {e}"

    def _lock(self, system: str) -> str:
        if system == "Windows":
            subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
        elif system == "Darwin":
            subprocess.Popen(["pmset", "displaysleepnow"])
        elif system == "Linux":
            for cmd in (
                ["loginctl", "lock-session"],
                ["gnome-screensaver-command", "-l"],
                ["xdg-screensaver", "lock"],
            ):
                try:
                    subprocess.Popen(cmd)
                    break
                except FileNotFoundError:
                    continue
        return "Screen locked"

    def _sleep(self, system: str) -> str:
        if system == "Windows":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        elif system == "Darwin":
            subprocess.Popen(["pmset", "sleepnow"])
        elif system == "Linux":
            subprocess.Popen(["systemctl", "suspend"])
        return "Sleeping..."

    def _shutdown(self, system: str) -> str:
        if system == "Windows":
            subprocess.Popen(["shutdown", "/s", "/t", "30"])
            return "Shutdown scheduled in 30 seconds. Run 'shutdown /a' to cancel."
        else:
            subprocess.Popen(["shutdown", "-h", "+1"])
            return "Shutdown scheduled in 1 minute. Run 'shutdown -c' to cancel."

    def _restart(self, system: str) -> str:
        if system == "Windows":
            subprocess.Popen(["shutdown", "/r", "/t", "30"])
            return "Restart scheduled in 30 seconds. Run 'shutdown /a' to cancel."
        else:
            subprocess.Popen(["shutdown", "-r", "+1"])
            return "Restart scheduled in 1 minute. Run 'shutdown -c' to cancel."

    def _mute(self, system: str) -> str:
        if system == "Windows":
            ps = "$obj = New-Object -ComObject WScript.Shell;$obj.SendKeys([char]173)"
            subprocess.Popen(["powershell", "-Command", ps])
        elif system == "Darwin":
            subprocess.Popen(["osascript", "-e", "set volume with output muted"])
        elif system == "Linux":
            subprocess.Popen(["amixer", "-D", "pulse", "sset", "Master", "mute"])
        return "Muted"

    def _unmute(self, system: str) -> str:
        if system == "Windows":
            ps = "$obj = New-Object -ComObject WScript.Shell;$obj.SendKeys([char]173)"
            subprocess.Popen(["powershell", "-Command", ps])
        elif system == "Darwin":
            subprocess.Popen(["osascript", "-e", "set volume without output muted"])
        elif system == "Linux":
            subprocess.Popen(["amixer", "-D", "pulse", "sset", "Master", "unmute"])
        return "Unmuted"

    def _volume(self, system: str, value: int) -> str:
        if not 0 <= value <= 100:
            return "Volume must be between 0 and 100"

        if system == "Windows":
            # Volume control on Windows requires nircmd or similar.
            # Use PowerShell with WScript to send volume keys.
            # Approximate: each press changes ~2%, so press ~50 times for full range.
            ps = f"""
$shell = New-Object -ComObject WScript.Shell
# Mute first to reset
1..50 | ForEach-Object {{ $shell.SendKeys([char]174) }}
# Unmute to start from 0
1..{value // 2} | ForEach-Object {{ $shell.SendKeys([char]175) }}
"""
            subprocess.Popen(["powershell", "-WindowStyle", "Hidden", "-Command", ps])
        elif system == "Darwin":
            subprocess.Popen(["osascript", "-e", f"set volume output volume {value}"])
        elif system == "Linux":
            subprocess.Popen(["amixer", "-D", "pulse", "sset", "Master", f"{value}%"])
        return f"Volume set to {value}%"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "lock",
                            "sleep",
                            "shutdown",
                            "restart",
                            "mute",
                            "unmute",
                            "volume",
                        ],
                        "description": "System control action",
                    },
                    "value": {
                        "type": "integer",
                        "description": "Volume level 0-100 (for 'volume' action)",
                    },
                },
                "required": ["action"],
            },
        }
