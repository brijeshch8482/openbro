"""Base tool interface with risk classification."""

from abc import ABC, abstractmethod
from enum import Enum


class RiskLevel(str, Enum):
    """Tool risk classification.

    SAFE: Read-only, no system changes (file read, web search, system info)
    MODERATE: Modifies user files or opens apps (file write, open app, download)
    DANGEROUS: System-level changes or irreversible actions (shell, shutdown, delete)
    """

    SAFE = "safe"
    MODERATE = "moderate"
    DANGEROUS = "dangerous"


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    risk: RiskLevel = RiskLevel.SAFE

    @abstractmethod
    def run(self, **kwargs) -> str: ...

    @abstractmethod
    def schema(self) -> dict: ...
