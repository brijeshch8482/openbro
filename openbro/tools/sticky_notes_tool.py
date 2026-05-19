"""Windows Sticky Notes integration — read, list, open.

Microsoft Sticky Notes is a built-in Windows 10/11 app. Its notes live
in a SQLite database at:
  %LOCALAPPDATA%\\Packages\\Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe\\LocalState\\plum.sqlite

This tool reads that DB (safe — read-only access doesn't disturb the
app) and can launch the app via the ms-stickynotes: URI handler.

Real-user incident this addresses: user asked the agent to write to
'stickynote', the model picked the `memory` tool (OpenBro's internal
user-facts DB) by mistake and reported success — the note never went
anywhere the user could see. Memory and Sticky Notes are different
storage; this tool makes the distinction explicit.

Add (write) action is opt-in via 'add' — it appends to the SQLite
notes table while the app is closed (Sticky Notes writes on app exit,
so editing while it's open is racy). For interactive add we launch
the app and copy the desired text to the clipboard so the user can
paste with Ctrl+V into a new note.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import subprocess
from pathlib import Path

from openbro.tools.base import BaseTool, RiskLevel

STICKY_NOTES_DB = "Packages/Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe/LocalState/plum.sqlite"


def _db_path() -> Path | None:
    if platform.system() != "Windows":
        return None
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    p = Path(local) / STICKY_NOTES_DB
    return p if p.exists() else None


class StickyNotesTool(BaseTool):
    name = "sticky_notes"
    description = (
        "Read or open Microsoft Sticky Notes (Windows built-in app). "
        "Actions: 'list' (show existing notes), 'open' (launch the app), "
        "'add' (copy text to clipboard + open app so user can paste). "
        "This is the Windows Sticky Notes app — NOT OpenBro's internal "
        "memory tool. Use this when the user says 'sticky note', "
        "'stickynote', 'note app', 'reminder note'."
    )
    risk = RiskLevel.SAFE

    def run(self, action: str = "list", text: str = "") -> str:
        action = action.lower().strip()
        if platform.system() != "Windows":
            return "Sticky Notes is a Windows-only app."

        if action == "open":
            return self._open_app()
        if action == "list":
            return self._list_notes()
        if action == "add":
            return self._add_note(text)
        return f"Unknown action: {action}. Available: list, open, add"

    def _open_app(self) -> str:
        try:
            # Universal Windows Platform protocol handler.
            subprocess.run(["cmd", "/c", "start", "", "ms-stickynotes:"], check=False)
            return "Sticky Notes app khol diya."
        except Exception as e:
            return f"App khol nahi paya: {e}"

    def _list_notes(self) -> str:
        db = _db_path()
        if db is None:
            return (
                "Sticky Notes DB nahi mila. Either app installed nahi hai "
                "ya tu pehli baar khol raha hai (DB tab create hota hai)."
            )
        try:
            # Open read-only so we don't disturb the app if it's open.
            uri = f"file:{db.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            try:
                cur = conn.execute(
                    "SELECT Text FROM Note WHERE IsDeleted = 0 ORDER BY UpdatedAt DESC"
                )
                rows = cur.fetchall()
            finally:
                conn.close()
        except sqlite3.Error as e:
            return f"DB read fail: {e}"

        if not rows:
            return "Koi sticky note nahi mili."
        out = [f"{len(rows)} sticky notes:"]
        for i, (text,) in enumerate(rows[:20], 1):
            snippet = (text or "").strip().replace("\n", " ")[:120]
            out.append(f"  {i}. {snippet}")
        if len(rows) > 20:
            out.append(f"  (+{len(rows) - 20} more)")
        return "\n".join(out)

    def _add_note(self, text: str) -> str:
        if not text:
            return "'text' is required (the content for the new note)."
        # Copy text to clipboard then open the app; the user pastes Ctrl+V
        # into a new note. Direct DB writes while the app is open get
        # silently overwritten on app exit — clipboard is safer.
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value @'\n{text}\n'@"],
                check=False,
            )
        except Exception as e:
            return f"Clipboard fail: {e}"
        self._open_app()
        preview = text[:60] + ("..." if len(text) > 60 else "")
        return (
            f'Text clipboard pe copy kar diya ("{preview}") aur Sticky Notes khol diya. '
            "Naya note me Ctrl+V se paste kar bhai."
        )

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "open", "add"],
                        "description": (
                            "list=read existing notes, open=launch app, add=copy text+open"
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Note content for action='add'. Copied to clipboard.",
                    },
                },
                "required": ["action"],
            },
        }
