"""Skill registry - loads built-in and user skills, manages their tools."""

import importlib.util
import sys
from pathlib import Path

from openbro.skills.base import BaseSkill
from openbro.tools.base import BaseTool
from openbro.utils.storage import get_storage_paths


class SkillRegistry:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._skills: dict[str, BaseSkill] = {}
        self._load_builtins()
        self._load_user_skills()

    def _load_builtins(self):
        from openbro.skills.builtin.github import GitHubSkill
        from openbro.skills.builtin.gmail import GmailSkill
        from openbro.skills.builtin.google_calendar import GoogleCalendarSkill
        from openbro.skills.builtin.notion import NotionSkill
        from openbro.skills.builtin.youtube import YouTubeSkill

        for cls in [GitHubSkill, GmailSkill, GoogleCalendarSkill, NotionSkill, YouTubeSkill]:
            try:
                skill = cls(config=self.config)
                self._skills[skill.name] = skill
            except Exception as e:
                print(f"Failed to load skill {cls.__name__}: {e}")

    def _load_user_skills(self):
        """Auto-load user skills from ~/.openbro/skills/<name>/skill.py."""
        try:
            paths = get_storage_paths()
            skills_dir = paths.get("skills")
        except Exception:
            return
        if not skills_dir or not Path(skills_dir).exists():
            return

        for entry in Path(skills_dir).iterdir():
            if not entry.is_dir():
                continue
            skill_file = entry / "skill.py"
            if not skill_file.exists():
                continue
            try:
                self._load_skill_file(skill_file)
            except Exception as e:
                print(f"Failed to load user skill {entry.name}: {e}")

    def _load_skill_file(self, skill_file: Path):
        mod_name = f"openbro_user_skill_{skill_file.parent.name}"
        spec = importlib.util.spec_from_file_location(mod_name, skill_file)
        if not spec or not spec.loader:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, BaseSkill) and attr is not BaseSkill:
                skill = attr(config=self.config)
                self._skills[skill.name] = skill

    def list_skills(self) -> list[BaseSkill]:
        return list(self._skills.values())

    def get_skill(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def all_tools(self, only_configured: bool = True) -> list[BaseTool]:
        """Return tools from all skills (or only configured ones)."""
        tools: list[BaseTool] = []
        for skill in self._skills.values():
            if only_configured and not skill.is_configured():
                continue
            tools.extend(skill.tools())
        return tools

    def info(self) -> list[dict]:
        return [s.info() for s in self._skills.values()]
