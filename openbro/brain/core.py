"""Brain — the v2 intelligence layer's main orchestrator.

This is the public face of the Brain. The agent talks to this object;
it hides the storage, profile, memory, skills, and reflection modules
behind a single ergonomic surface.

Usage:
    from openbro.brain import Brain
    brain = Brain.load()                 # auto-creates if missing
    brain.profile                        # UserProfile (read/write)
    brain.skills                         # SkillRegistry  (Phase 3)
    brain.memory                         # SemanticMemory (Phase 2)
    brain.record_interaction(...)        # update profile + log learning
    brain.export("backup.tar.gz")
    brain.stats()                        # human-readable health snapshot
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

from openbro.brain.profile import UserProfile
from openbro.brain.storage import BRAIN_VERSION, BrainStorage


class Brain:
    """Main orchestrator. Holds storage + profile + (eventually) memory + skills."""

    def __init__(self, storage: BrainStorage, profile: UserProfile):
        self.storage = storage
        self.profile = profile
        # Lazy-initialised modules — populated as later phases ship
        self._memory = None
        self._skills = None
        self._self_coder = None

    # ─── construction ──────────────────────────────────────────────

    @classmethod
    def load(cls, brain_dir: Path | None = None) -> Brain:
        """Load (or create) the brain from disk."""
        storage = BrainStorage(brain_dir)
        profile = UserProfile.load(storage.profile_path)
        return cls(storage=storage, profile=profile)

    # ─── persistence ───────────────────────────────────────────────

    def save(self) -> None:
        """Persist everything that's mutable to disk."""
        self.profile.save(self.storage.profile_path)
        self.storage.update_meta(
            patterns_count=self._patterns_count(),
            skills_count=self._skills_count(),
        )

    def _patterns_count(self) -> int:
        # Will be wired up in Phase 2 (semantic memory)
        return 0

    def _skills_count(self) -> int:
        # Count skill files when skills system lands (Phase 3)
        return len([p for p in self.storage.skills_dir.glob("*.py") if p.name != "__init__.py"])

    # ─── interaction tracking ──────────────────────────────────────

    def record_interaction(
        self,
        prompt: str,
        response: str,
        language: str | None = None,
        tools_used: list[str] | None = None,
        success: bool = True,
    ) -> None:
        """Called after every user turn. Updates profile + appends to learnings."""
        self.profile.record_interaction(lang=language)

        self.storage.append_learning(
            {
                "type": "interaction",
                "language": language,
                "tools_used": tools_used or [],
                "success": success,
                "prompt_len": len(prompt),
                "response_len": len(response),
            }
        )
        self.save()

    # ─── community sync (Phase 8) ──────────────────────────────────

    def update(self) -> dict:
        """Pull community patterns from github.com/openbro/openbro-brain.

        Stub for now; full implementation lands in Phase 8 (Brain Updater).
        Returns a summary dict so the CLI can report what happened.
        """
        # TODO: clone/pull openbro-brain repo, merge patterns + skills,
        # verify safety in sandbox, apply.
        return {
            "status": "not_implemented",
            "message": "brain update will land in v2 Phase 8.",
        }

    # ─── daily LLM-update check ────────────────────────────────────

    def check_for_better_llm(
        self,
        current: tuple[str, str],
        config: dict | None = None,
        force: bool = False,
    ) -> dict | None:
        """Once-a-day online check: 'is there a better LLM available than what
        the user is on right now?' Returns the suggestion dict or None.

        force=True bypasses the 24-hour cooldown (used by 'brain update' CLI).
        """
        from datetime import datetime, timezone

        from openbro.llm.auto_select import suggest_upgrade

        meta = self.storage.read_meta()
        last_check = meta.get("last_llm_check")
        if not force and last_check:
            try:
                prev = datetime.fromisoformat(last_check)
                hours = (datetime.now(timezone.utc) - prev).total_seconds() / 3600
                if hours < 24:
                    return None  # already checked today
            except ValueError:
                pass

        suggestion = suggest_upgrade(current, config)
        self.storage.update_meta(last_llm_check=datetime.now(timezone.utc).isoformat())
        return suggestion

    # ─── export / import ───────────────────────────────────────────

    def export(self, output_path: str | Path) -> Path:
        """Tar.gz the brain directory for backup or transfer."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out, "w:gz") as tar:
            tar.add(self.storage.dir, arcname="brain")
        return out

    def import_from(self, archive_path: str | Path, replace: bool = False) -> None:
        """Restore a brain from a tar.gz archive.

        replace=True wipes the existing brain dir first.
        """
        archive = Path(archive_path)
        if not archive.exists():
            raise FileNotFoundError(archive)
        if replace:
            shutil.rmtree(self.storage.dir, ignore_errors=True)
            self.storage.dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            # Strip the leading "brain/" prefix added by export()
            for member in tar.getmembers():
                if member.name.startswith("brain/"):
                    member.name = member.name[len("brain/") :]
                # Skip empty / parent paths
                if not member.name:
                    continue
                tar.extract(member, self.storage.dir)
        # Reload profile after restore
        self.profile = UserProfile.load(self.storage.profile_path)

    # ─── stats / introspection ────────────────────────────────────

    def stats(self) -> dict:
        meta = self.storage.read_meta()
        return {
            "version": BRAIN_VERSION,
            "brain_id": meta.get("brain_id"),
            "created_at": meta.get("created_at"),
            "last_update": meta.get("last_update"),
            "interaction_count": self.profile.interaction_count,
            "patterns": self._patterns_count(),
            "skills": self._skills_count(),
            "size_kb": self.storage.total_size_bytes() // 1024,
            "profile_summary": self.profile.context_snippet(),
        }
