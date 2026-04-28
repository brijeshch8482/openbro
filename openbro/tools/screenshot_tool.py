"""Screenshot tool - capture the screen and save to a file."""

import platform
import subprocess
from datetime import datetime
from pathlib import Path

from openbro.tools.base import BaseTool, RiskLevel


class ScreenshotTool(BaseTool):
    name = "screenshot"
    description = "Take a screenshot of the screen and save it to a file"
    risk = RiskLevel.SAFE

    def run(self, dest_folder: str = "", filename: str = "") -> str:
        if dest_folder:
            dest = Path(dest_folder).expanduser().resolve()
        else:
            dest = Path.home() / "Pictures" / "OpenBro Screenshots"
        dest.mkdir(parents=True, exist_ok=True)

        if not filename:
            filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        if not filename.lower().endswith((".png", ".jpg", ".jpeg")):
            filename += ".png"

        target = dest / filename
        system = platform.system()

        try:
            if system == "Windows":
                # Use PowerShell with .NET to capture screen
                escaped_path = str(target).replace("\\", "\\\\")
                ps_script = (
                    "Add-Type -AssemblyName System.Windows.Forms;"
                    "Add-Type -AssemblyName System.Drawing;"
                    "$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
                    "$bmp = New-Object System.Drawing.Bitmap "
                    "$bounds.Width, $bounds.Height;"
                    "$g = [System.Drawing.Graphics]::FromImage($bmp);"
                    "$g.CopyFromScreen($bounds.Location, "
                    "[System.Drawing.Point]::Empty, $bounds.Size);"
                    f"$bmp.Save('{escaped_path}');"
                    "$g.Dispose();"
                    "$bmp.Dispose()"
                )
                result = subprocess.run(
                    ["powershell", "-Command", ps_script],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode != 0:
                    return f"Screenshot failed: {result.stderr.strip()}"

            elif system == "Darwin":
                subprocess.run(["screencapture", "-x", str(target)], check=True, timeout=15)

            elif system == "Linux":
                # Try gnome-screenshot, then scrot, then import (ImageMagick)
                for cmd in (
                    ["gnome-screenshot", "-f", str(target)],
                    ["scrot", str(target)],
                    ["import", "-window", "root", str(target)],
                ):
                    try:
                        subprocess.run(cmd, check=True, timeout=15)
                        break
                    except FileNotFoundError:
                        continue
                else:
                    return "Install gnome-screenshot, scrot, or imagemagick on Linux"

            if target.exists():
                size_kb = target.stat().st_size / 1024
                return f"Screenshot saved: {target} ({size_kb:.1f} KB)"
            return "Screenshot command ran but file not found"

        except Exception as e:
            return f"Screenshot failed: {e}"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "dest_folder": {
                        "type": "string",
                        "description": (
                            "Folder to save screenshot. Default: ~/Pictures/OpenBro Screenshots"
                        ),
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename. Default: timestamped name",
                    },
                },
                "required": [],
            },
        }
