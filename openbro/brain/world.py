"""world.json — static facts about the user's PC environment.

Snapshot of things the agent shouldn't have to keep re-asking about:
  - OS, hostname, user
  - Common paths (Desktop, Documents, Downloads)
  - Installed apps (best-effort detection)
  - Network state (online/offline at last refresh)

Refreshed on startup + on demand via `brain refresh-world`. Injected into
the LLM system prompt so the model knows the user's environment without
calling tools every turn.
"""

from __future__ import annotations

import getpass
import json
import platform
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path

# Apps to look for via shutil.which / common install paths
COMMON_APPS = [
    "code",
    "code-insiders",
    "chrome",
    "firefox",
    "edge",
    "brave",
    "git",
    "node",
    "npm",
    "python",
    "pip",
    "docker",
    "kubectl",
    "claude",
    "codex",
    "aider",
    "gemini",
    "ollama",
    "spotify",
    "discord",
    "slack",
    "notion",
]


def detect_paths() -> dict:
    """Return common user-folder paths if they exist."""
    home = Path.home()
    candidates = {
        "home": home,
        "desktop": home / "Desktop",
        "documents": home / "Documents",
        "downloads": home / "Downloads",
        "pictures": home / "Pictures",
        "videos": home / "Videos",
    }
    return {k: str(v) for k, v in candidates.items() if v.exists()}


def detect_apps() -> dict:
    """Best-effort check for common apps on PATH or in common locations."""
    found = {}
    for app in COMMON_APPS:
        path = shutil.which(app)
        if path:
            found[app] = path
    if platform.system() == "Windows":
        # Also check the Apps directory on Windows
        local_programs = Path.home() / "AppData" / "Local" / "Programs"
        if local_programs.exists():
            for child in local_programs.iterdir():
                if child.is_dir() and child.name.lower() not in (a.lower() for a in found):
                    found[child.name] = str(child)
    return found


def is_online(timeout: float = 1.0) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname("github.com")
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(None)


def snapshot() -> dict:
    """Build the full world snapshot."""
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "user": {
            "name": getpass.getuser(),
            "hostname": socket.gethostname(),
        },
        "paths": detect_paths(),
        "apps": detect_apps(),
        "online": is_online(),
    }


def refresh(brain) -> dict:
    """Capture a fresh snapshot and persist it to brain/world.json."""
    data = snapshot()
    try:
        brain.storage.world_path.write_text(json.dumps(data, indent=2))
    except OSError:
        pass
    return data


def load(brain) -> dict:
    """Load the cached world snapshot, refreshing if missing or older than 6h."""
    p = brain.storage.world_path
    if p.exists():
        try:
            data = json.loads(p.read_text())
            ts_str = data.get("captured_at", "")
            ts = datetime.fromisoformat(ts_str)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            if age_h < 6:
                return data
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    return refresh(brain)


def context_snippet(world: dict) -> str:
    """Compact, LLM-friendly slice of the world for system-prompt injection."""
    if not world:
        return ""
    os_info = world.get("os", {})
    user = world.get("user", {})
    paths = world.get("paths", {})
    apps = world.get("apps", {})
    lines = [
        f"User environment: {os_info.get('system', '?')} {os_info.get('release', '')}, "
        f"user={user.get('name', '?')}",
        "Common paths: " + ", ".join(f"{k}={v}" for k, v in list(paths.items())[:5]),
    ]
    if apps:
        lines.append("Installed apps: " + ", ".join(list(apps.keys())[:12]))
    lines.append("Online: " + ("yes" if world.get("online") else "no"))
    return "\n".join(lines)
