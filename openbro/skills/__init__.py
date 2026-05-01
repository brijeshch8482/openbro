"""OpenBro Skills - plugin system for extending OpenBro with new capabilities."""

from openbro.skills.base import BaseSkill
from openbro.skills.registry import SkillRegistry

__all__ = ["BaseSkill", "SkillRegistry"]
