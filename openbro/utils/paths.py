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
import re
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


def resolve_with_candidates(raw: str | os.PathLike) -> Path:
    """Like resolve_user_path, but when the resolved path doesn't
    exist, try several common rewrites BEFORE returning. Maps to
    Claude-style behaviour: 'if my first guess doesn't work, try
    others before giving up'.

    Captured 2026-05-31: user said `C:\\OneDrive\\Desktop\\Testing
    logs\\30th log` (a real symlink path on their machine), but a
    Path.exists() check via expanduser missed it because the symlink
    isn't resolved by expanduser alone. Agent then claimed 'folder
    not found' even though the data was right there.

    Candidates tried, in order:
      1. The straight resolve_user_path output (existing behaviour)
      2. `C:\\OneDrive\\...`  → `C:\\Users\\<user>\\OneDrive\\...`
         (reverse symlink expansion)
      3. `C:\\Users\\<user>\\OneDrive\\...`  →  `C:\\OneDrive\\...`
         (forward symlink contraction)
      4. Case-insensitive match against the parent directory's
         actual entries (handles `30th log` vs `30th Log`)

    Returns the first candidate that exists. If none exist, returns
    the original resolved path so callers can still report 'not
    found' with the original input echoed back.
    """
    primary = resolve_user_path(raw)
    if primary.exists():
        return primary

    candidates: list[Path] = [primary]
    raw_str = str(raw)

    # Symlink contraction / expansion between C:\OneDrive and
    # C:\Users\<user>\OneDrive — Windows often exposes BOTH paths on
    # OneDrive-enabled accounts.
    if platform.system() == "Windows":
        home = Path.home()
        # Expand `C:\OneDrive\...` to `C:\Users\<user>\OneDrive\...`
        m = re.match(r"^([A-Za-z]):[\\/]+OneDrive[\\/](.*)$", raw_str)
        if m:
            drive, rest = m.group(1), m.group(2)
            cand = Path(home) / "OneDrive" / rest
            candidates.append(cand)
            # Also try the configured OneDrive roots.
            for od in _onedrive_roots():
                candidates.append(od / rest)
            _ = drive  # silence unused

        # Contract `C:\Users\<user>\OneDrive\...` to `C:\OneDrive\...`
        try:
            rel = Path(raw_str).expanduser().relative_to(home / "OneDrive")
            for prefix in (Path("C:/OneDrive"), Path("D:/OneDrive")):
                candidates.append(prefix / rel)
        except (ValueError, OSError):
            pass

    # Case-insensitive parent walk: walk up to a parent that exists,
    # then case-insensitively match each remaining segment against
    # whatever's actually there.
    walk_target = primary
    walked_back: list[str] = []
    while walk_target and not walk_target.exists():
        walked_back.append(walk_target.name)
        if walk_target.parent == walk_target:
            walk_target = None
            break
        walk_target = walk_target.parent
    if walk_target is not None and walked_back:
        cur = walk_target
        ok = True
        for seg in reversed(walked_back):
            try:
                entries = {e.name.lower(): e for e in cur.iterdir()}
            except (PermissionError, OSError):
                ok = False
                break
            match = entries.get(seg.lower())
            if match is None:
                ok = False
                break
            cur = match
        if ok and cur.exists():
            candidates.append(cur)

    # Return the first candidate that exists.
    for c in candidates:
        try:
            if c.exists():
                return c.resolve()
        except (PermissionError, OSError):
            continue

    # No candidate exists — return the primary resolution so the
    # caller can report 'not found' with the original input echoed.
    return primary
