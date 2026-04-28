"""Clipboard tool - read from and write to system clipboard."""

import platform
import subprocess

from openbro.tools.base import BaseTool, RiskLevel


class ClipboardTool(BaseTool):
    name = "clipboard"
    description = "Read from or write text to the system clipboard"
    risk = RiskLevel.SAFE

    def run(self, action: str, text: str = "") -> str:
        if action == "copy":
            return self._copy(text)
        elif action == "paste":
            return self._paste()
        else:
            return f"Unknown action: {action}. Available: copy, paste"

    def _copy(self, text: str) -> str:
        if not text:
            return "Text required for copy"
        system = platform.system()
        try:
            if system == "Windows":
                proc = subprocess.Popen(
                    ["clip"],
                    stdin=subprocess.PIPE,
                    shell=False,
                )
                proc.communicate(input=text.encode("utf-16le"))
            elif system == "Darwin":
                proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                proc.communicate(input=text.encode("utf-8"))
            else:
                # Linux - try xclip then xsel
                for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]):
                    try:
                        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                        proc.communicate(input=text.encode("utf-8"))
                        break
                    except FileNotFoundError:
                        continue
                else:
                    return "Install 'xclip' or 'xsel' to use clipboard on Linux"

            preview = text[:50] + ("..." if len(text) > 50 else "")
            return f"Copied to clipboard: {preview}"
        except Exception as e:
            return f"Copy failed: {e}"

    def _paste(self) -> str:
        system = platform.system()
        try:
            if system == "Windows":
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Clipboard"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return f"Clipboard content:\n{result.stdout.rstrip()}"
            elif system == "Darwin":
                result = subprocess.run(["pbpaste"], capture_output=True, text=True)
                return f"Clipboard content:\n{result.stdout}"
            else:
                for cmd in (["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b", "-o"]):
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True)
                        return f"Clipboard content:\n{result.stdout}"
                    except FileNotFoundError:
                        continue
                return "Install 'xclip' or 'xsel' to use clipboard on Linux"
        except Exception as e:
            return f"Paste failed: {e}"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["copy", "paste"],
                        "description": "copy text to clipboard, or paste current clipboard",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to copy (for copy action)",
                    },
                },
                "required": ["action"],
            },
        }
