"""Storage manager - handles data, memory, and model paths with custom drive support."""

import os
import shutil
from pathlib import Path

from openbro.utils.config import get_config_dir, load_config, save_config


def get_storage_paths() -> dict:
    """Get all storage paths based on config."""
    config = load_config()
    storage = config.get("storage", {})

    base_dir = Path(storage.get("base_dir", str(get_config_dir())))
    base_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "base": base_dir,
        "memory": base_dir / "memory",
        "history": base_dir / "history.txt",
        "logs": base_dir / "logs",
        "cache": base_dir / "cache",
        "skills": base_dir / "skills",
        "models": Path(storage.get("models_dir", str(base_dir / "models"))),
    }

    # Create directories
    for key in ("memory", "logs", "cache", "skills", "models"):
        paths[key].mkdir(parents=True, exist_ok=True)

    return paths


def get_available_drives() -> list[dict]:
    """Get available drives with free space info (Windows + Linux/Mac)."""
    drives = []

    if os.name == "nt":
        # Windows - check common drive letters
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive_path = f"{letter}:\\"
            if os.path.exists(drive_path):
                try:
                    total, used, free = shutil.disk_usage(drive_path)
                    drives.append(
                        {
                            "path": drive_path,
                            "name": f"{letter}:",
                            "total_gb": round(total / (1024**3), 1),
                            "free_gb": round(free / (1024**3), 1),
                            "used_percent": round((used / total) * 100, 1),
                        }
                    )
                except (PermissionError, OSError):
                    pass
    else:
        # Linux/Mac - check home and common mount points
        for mount in ["/", os.path.expanduser("~"), "/mnt", "/media"]:
            if os.path.exists(mount):
                try:
                    total, used, free = shutil.disk_usage(mount)
                    drives.append(
                        {
                            "path": mount,
                            "name": mount,
                            "total_gb": round(total / (1024**3), 1),
                            "free_gb": round(free / (1024**3), 1),
                            "used_percent": round((used / total) * 100, 1),
                        }
                    )
                except (PermissionError, OSError):
                    pass

    return drives


def detect_cloud_folders() -> list[dict]:
    """Detect cloud sync folders (Google Drive, OneDrive, Dropbox)."""
    home = Path.home()
    cloud_folders = []

    candidates = [
        ("Google Drive", home / "Google Drive"),
        ("Google Drive", home / "My Drive"),
        ("Google Drive", Path("G:/My Drive")),
        ("OneDrive", home / "OneDrive"),
        ("Dropbox", home / "Dropbox"),
        ("iCloud", home / "iCloudDrive"),
    ]

    # Windows-specific Google Drive paths
    if os.name == "nt":
        for letter in "GHIJKLMNOPQRSTUVWXYZ":
            gd_path = Path(f"{letter}:/My Drive")
            if gd_path.exists():
                candidates.append(("Google Drive", gd_path))

    for name, path in candidates:
        if path.exists() and path.is_dir():
            try:
                total, used, free = shutil.disk_usage(str(path))
                cloud_folders.append(
                    {
                        "name": name,
                        "path": str(path),
                        "free_gb": round(free / (1024**3), 1),
                    }
                )
            except (PermissionError, OSError):
                cloud_folders.append(
                    {
                        "name": name,
                        "path": str(path),
                        "free_gb": 0,
                    }
                )

    return cloud_folders


def set_storage_path(base_dir: str, models_dir: str | None = None):
    """Update storage paths in config."""
    config = load_config()

    if "storage" not in config:
        config["storage"] = {}

    config["storage"]["base_dir"] = base_dir
    if models_dir:
        config["storage"]["models_dir"] = models_dir

    save_config(config)

    # Create directories immediately
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    if models_dir:
        Path(models_dir).mkdir(parents=True, exist_ok=True)


def migrate_storage(old_base: str, new_base: str):
    """Move data from old storage location to new one."""
    old_path = Path(old_base)
    new_path = Path(new_base)

    if not old_path.exists():
        return

    new_path.mkdir(parents=True, exist_ok=True)

    for item in old_path.iterdir():
        dest = new_path / item.name
        if item.is_dir():
            if dest.exists():
                shutil.copytree(str(item), str(dest), dirs_exist_ok=True)
            else:
                shutil.copytree(str(item), str(dest))
        else:
            shutil.copy2(str(item), str(dest))


def get_storage_size() -> dict:
    """Get current storage usage."""
    paths = get_storage_paths()
    sizes = {}

    for key, path in paths.items():
        if isinstance(path, Path):
            if path.is_dir():
                total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                sizes[key] = total
            elif path.is_file():
                sizes[key] = path.stat().st_size
            else:
                sizes[key] = 0

    return sizes


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.1f} MB"
    else:
        return f"{size_bytes / 1024**3:.2f} GB"
