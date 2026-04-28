"""Notification tool - show desktop notifications to the user."""

import platform
import subprocess

from openbro.tools.base import BaseTool, RiskLevel


class NotificationTool(BaseTool):
    name = "notification"
    description = "Show a desktop notification with a title and message"
    risk = RiskLevel.SAFE

    def run(self, title: str, message: str = "") -> str:
        if not title:
            return "Title required"

        system = platform.system()
        try:
            if system == "Windows":
                # Use PowerShell with BurntToast-free fallback via System.Windows.Forms
                ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
$balloon = New-Object System.Windows.Forms.NotifyIcon
$balloon.Icon = [System.Drawing.SystemIcons]::Information
$balloon.BalloonTipTitle = '{self._escape(title)}'
$balloon.BalloonTipText = '{self._escape(message)}'
$balloon.Visible = $true
$balloon.ShowBalloonTip(5000)
Start-Sleep -Seconds 5
$balloon.Dispose()
"""
                subprocess.Popen(
                    ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            elif system == "Darwin":
                osa = (
                    f'display notification "{self._escape(message)}" '
                    f'with title "{self._escape(title)}"'
                )
                subprocess.Popen(["osascript", "-e", osa])

            elif system == "Linux":
                subprocess.Popen(["notify-send", title, message])

            return f"Notification shown: {title}"

        except Exception as e:
            return f"Notification failed: {e}"

    def _escape(self, text: str) -> str:
        return text.replace("'", "''").replace('"', '\\"')

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Notification title (required)",
                    },
                    "message": {
                        "type": "string",
                        "description": "Notification body text",
                    },
                },
                "required": ["title"],
            },
        }
