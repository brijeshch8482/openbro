"""User-folder path resolution with OneDrive redirect awareness.

On Windows with OneDrive sync, the user's real Desktop / Documents /
Pictures / Downloads / Videos live under `%USERPROFILE%\\OneDrive\\` —
not under `%USERPROFILE%\\` directly. Tools that do `Path('~/Desktop')
.expanduser()` resolve to the system folder (often empty), while the
user is looking at the OneDrive one in Explorer. Files get "created"
where the user can't see them.

`resolve_user_path()` fixes this: if the path starts with one of the
five OneDrive-redirectable folder names under the home directory, and
the OneDrive copy exists, use that. Otherwise behave like a normal
expanduser + resolve.

Real-user incident that motivated this: agent created `new_file.docx`
at `C:\\Users\\X\\Desktop` after the user asked for "Desktop par file
banao" while Explorer was showing `C:\\Users\\X\\OneDrive\\Desktop`.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

ONEDRIVE_REDIRECTED = ("Desktop", "Documents", "Downloads", "Pictures", "Videos")


def _onedrive_roots() -> list[Path]:
    """OneDrive root folder(s) that actually exist on this machine."""
    if platform.system() != "Windows":
        return []
    roots: list[Path] = []
    for env in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        v = os.environ.get(env)
        if v:
            p = Path(v)
            if p.exists() and p not in roots:
                roots.append(p)
    return roots


def resolve_user_path(raw: str | os.PathLike) -> Path:
    """Resolve a path, preferring OneDrive-redirected user folders on Windows.

    Examples:
      '~/Desktop/x.docx'  -> 'C:/Users/X/OneDrive/Desktop/x.docx' if OD exists
                            else 'C:/Users/X/Desktop/x.docx'
      'C:/abs/path'       -> 'C:/abs/path' unchanged
      'relative.txt'      -> '<cwd>/relative.txt'
    """
    p = Path(raw).expanduser()

    # Only attempt redirect rewriting if the path is anchored at the user's
    # home and points at one of the known redirectable folders.
    if platform.system() == "Windows":
        try:
            home = Path.home()
            rel = p.relative_to(home)
        except ValueError:
            rel = None
        if rel is not None and rel.parts and rel.parts[0] in ONEDRIVE_REDIRECTED:
            for od in _onedrive_roots():
                candidate = od / rel
                # Prefer OneDrive if the folder exists there. We check the
                # parent so we redirect new-file creations into the right
                # place too (the file itself won't exist yet).
                if candidate.parent.exists():
                    return candidate.resolve()

    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()
