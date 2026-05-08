"""Brain storage layout — paths, directory creation, file I/O.

Layout (under `~/.openbro/brain/` by default):
    profile.yaml      - user model
    memory.db         - SQLite-vec semantic memory
    skills/           - auto-generated executable Python files
    world.json        - static facts about the user's environment
    learnings.jsonl   - append-only log of learning events
    meta.json         - version, brain_id, timestamps
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from openbro.utils.storage import get_storage_paths

BRAIN_VERSION = "2.0.0"


def get_brain_dir() -> Path:
    """Return the brain directory, honoring custom storage paths from v1 config."""
    paths = get_storage_paths()
    base = Path(paths.get("base", Path.home() / ".openbro"))
    brain = base / "brain"
    brain.mkdir(parents=True, exist_ok=True)
    return brain


class BrainStorage:
    """Thin wrapper around the brain directory — handles layout + meta + skills dir."""

    def __init__(self, brain_dir: Path | None = None):
        self.dir = brain_dir or get_brain_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

        # Subpaths
        self.profile_path = self.dir / "profile.yaml"
        self.memory_db_path = self.dir / "memory.db"
        self.skills_dir = self.dir / "skills"
        self.world_path = self.dir / "world.json"
        self.learnings_path = self.dir / "learnings.jsonl"
        self.meta_path = self.dir / "meta.json"

        # Ensure skills/ exists
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        # Initialize meta.json on first run
        if not self.meta_path.exists():
            self._init_meta()

    def _init_meta(self) -> None:
        meta = {
            "version": BRAIN_VERSION,
            "brain_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_update": datetime.now(timezone.utc).isoformat(),
            "last_community_sync": None,
            "patterns_count": 0,
            "skills_count": 0,
        }
        self.meta_path.write_text(json.dumps(meta, indent=2))

    def read_meta(self) -> dict:
        if not self.meta_path.exists():
            self._init_meta()
        try:
            return json.loads(self.meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            self._init_meta()
            return json.loads(self.meta_path.read_text())

    def update_meta(self, **kwargs) -> None:
        meta = self.read_meta()
        meta.update(kwargs)
        meta["last_update"] = datetime.now(timezone.utc).isoformat()
        self.meta_path.write_text(json.dumps(meta, indent=2))

    def append_learning(self, event: dict) -> None:
        """Append a learning event to learnings.jsonl (one JSON per line)."""
        event["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self.learnings_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_learnings(self, limit: int | None = None) -> list[dict]:
        if not self.learnings_path.exists():
            return []
        events = []
        with open(self.learnings_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if limit:
            events = events[-limit:]
        return events

    def total_size_bytes(self) -> int:
        """Approximate disk footprint of the brain (for `brain stats`)."""
        total = 0
        for f in self.dir.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    continue
        return total
