"""Base skill interface - skills are plugin packages that register tools."""

from abc import ABC, abstractmethod

from openbro.tools.base import BaseTool


class BaseSkill(ABC):
    """A Skill is a collection of related tools + config that extends OpenBro.

    Examples: github (search repos, list issues), gmail (send email),
    youtube (get transcript), notion (create page).

    Subclasses must:
    - Set name, description, version
    - Implement tools() returning list of BaseTool instances
    - Optionally declare config_keys (e.g. ["github.token"]) for API access
    """

    name: str = ""
    description: str = ""
    version: str = "0.1.0"
    author: str = ""
    config_keys: list[str] = []

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def tools(self) -> list[BaseTool]:
        """Return list of tool instances this skill provides."""
        ...

    def is_configured(self) -> bool:
        """Check if all required config keys are present and non-empty."""
        for key in self.config_keys:
            value = self._get_nested(self.config, key)
            if not value:
                return False
        return True

    @staticmethod
    def _get_nested(d: dict, dotted_key: str):
        cur = d
        for part in dotted_key.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
            if cur is None:
                return None
        return cur

    def setup(self) -> None:
        """Optional initialization hook (e.g. validate credentials)."""
        pass

    def teardown(self) -> None:
        """Optional cleanup hook."""
        pass

    def info(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "configured": self.is_configured(),
            "tools": [t.name for t in self.tools()],
        }
