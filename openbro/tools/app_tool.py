"""App control tool - open, close, and find applications on the system."""

import os
import platform
import shutil
import subprocess

from openbro.tools.base import BaseTool, RiskLevel

# Common app aliases for cross-platform name resolution
WINDOWS_APP_ALIASES = {
    "chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "edge": "msedge.exe",
    "vscode": "code.exe",
    "vs code": "code.exe",
    "code": "code.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "spotify": "spotify.exe",
    "discord": "discord.exe",
    "telegram": "telegram.exe",
    "whatsapp": "whatsapp.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "settings": "ms-settings:",
    "paint": "mspaint.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe",
}

LINUX_APP_ALIASES = {
    "chrome": "google-chrome",
    "firefox": "firefox",
    "vscode": "code",
    "vs code": "code",
    "code": "code",
    "files": "nautilus",
    "file manager": "nautilus",
    "terminal": "gnome-terminal",
    "calculator": "gnome-calculator",
    "settings": "gnome-control-center",
}

MAC_APP_ALIASES = {
    "chrome": "Google Chrome",
    "firefox": "Firefox",
    "safari": "Safari",
    "vscode": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "code": "Visual Studio Code",
    "finder": "Finder",
    "terminal": "Terminal",
    "calculator": "Calculator",
    "settings": "System Settings",
    "preferences": "System Preferences",
}


class AppTool(BaseTool):
    name = "app"
    description = (
        "Open, close, or find applications on the system. "
        "Use 'open' to launch apps like Chrome, VS Code, Spotify, etc."
    )
    risk = RiskLevel.MODERATE

    def run(self, action: str, app_name: str = "", args: str = "") -> str:
        if action == "open":
            return self._open_app(app_name, args)
        elif action == "close":
            return self._close_app(app_name)
        elif action == "find":
            return self._find_app(app_name)
        elif action == "list":
            return self._list_running()
        else:
            return f"Unknown action: {action}. Available: open, close, find, list"

    def _open_app(self, app_name: str, args: str = "") -> str:
        if not app_name:
            return "App name required"

        system = platform.system()
        resolved = self._resolve_app_name(app_name, system)

        try:
            if system == "Windows":
                if resolved.startswith("ms-settings:"):
                    os.startfile(resolved)
                    return f"Opened: {resolved}"

                # Try direct executable first, then via 'start' command
                cmd_args = args.split() if args else []
                try:
                    subprocess.Popen(
                        [resolved, *cmd_args],
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
                except FileNotFoundError:
                    # Fallback: use Windows 'start' shell command
                    full_cmd = f'start "" "{resolved}"'
                    if args:
                        full_cmd += f" {args}"
                    subprocess.Popen(full_cmd, shell=True)

            elif system == "Linux":
                cmd_args = args.split() if args else []
                subprocess.Popen([resolved, *cmd_args])

            elif system == "Darwin":
                cmd = ["open", "-a", resolved]
                if args:
                    cmd.extend(["--args", *args.split()])
                subprocess.Popen(cmd)

            return f"Opened: {resolved}"

        except FileNotFoundError:
            return (
                f"App '{app_name}' (resolved to '{resolved}') not found. "
                "Try 'find' action to locate it."
            )
        except Exception as e:
            return f"Failed to open '{app_name}': {e}"

    def _close_app(self, app_name: str) -> str:
        if not app_name:
            return "App name required"

        system = platform.system()
        try:
            if system == "Windows":
                exe = self._resolve_app_name(app_name, system)
                if not exe.endswith(".exe"):
                    exe = exe + ".exe" if "." not in exe else exe
                result = subprocess.run(
                    ["taskkill", "/F", "/IM", exe],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return f"Closed: {exe}"
                return f"Could not close {exe}: {result.stderr.strip()}"

            else:
                # Linux/Mac use pkill
                result = subprocess.run(
                    ["pkill", "-f", app_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return f"Closed processes matching: {app_name}"
                return f"No processes found for: {app_name}"

        except Exception as e:
            return f"Error closing app: {e}"

    def _find_app(self, app_name: str) -> str:
        if not app_name:
            return "App name required for find"

        system = platform.system()
        resolved = self._resolve_app_name(app_name, system)

        # Try shutil.which first
        path = shutil.which(resolved)
        if path:
            return f"Found: {path}"

        # Windows: search Program Files
        if system == "Windows":
            search_dirs = [
                os.environ.get("ProgramFiles", "C:\\Program Files"),
                os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
            ]
            target = resolved if resolved.endswith(".exe") else resolved + ".exe"
            target_lower = target.lower()
            for base in search_dirs:
                if not base or not os.path.exists(base):
                    continue
                for root, _, files in os.walk(base):
                    for f in files:
                        if f.lower() == target_lower:
                            return f"Found: {os.path.join(root, f)}"

        return f"Could not find '{app_name}'. It may not be installed."

    def _list_running(self) -> str:
        system = platform.system()
        try:
            if system == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                lines = result.stdout.strip().split("\n")[:30]
                names = []
                for line in lines:
                    parts = line.split('","')
                    if parts:
                        names.append(parts[0].strip('"'))
                return "Running apps (top 30):\n" + "\n".join(f"  - {n}" for n in names)
            else:
                result = subprocess.run(
                    ["ps", "-eo", "comm", "--no-headers"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                lines = result.stdout.strip().split("\n")[:30]
                return "Running apps (top 30):\n" + "\n".join(f"  - {line}" for line in lines)
        except Exception as e:
            return f"Error listing processes: {e}"

    def _resolve_app_name(self, name: str, system: str) -> str:
        name_lower = name.lower().strip()

        if system == "Windows":
            return WINDOWS_APP_ALIASES.get(name_lower, name)
        elif system == "Linux":
            return LINUX_APP_ALIASES.get(name_lower, name)
        elif system == "Darwin":
            return MAC_APP_ALIASES.get(name_lower, name)
        return name

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open", "close", "find", "list"],
                        "description": (
                            "Action: open an app, close it, find its path, or list running apps"
                        ),
                    },
                    "app_name": {
                        "type": "string",
                        "description": (
                            "Name of the app (e.g. 'chrome', 'vscode', 'spotify', "
                            "'notepad'). Aliases supported."
                        ),
                    },
                    "args": {
                        "type": "string",
                        "description": (
                            "Optional arguments to pass to the app "
                            "(e.g. URL for browser, file path for editor)"
                        ),
                    },
                },
                "required": ["action"],
            },
        }
