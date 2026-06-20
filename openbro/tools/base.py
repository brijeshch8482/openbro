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

    def compute_risk(self, args: dict) -> RiskLevel:
        """Per-call risk classification.

        Default: return the class-level `risk`. Tools that can run
        either trivially or destructively (the prime case is `shell`:
        `Get-Process` vs `Remove-Item -Recurse -Force C:\\Windows\\Temp`)
        override this so the permission gate sees the right tier and
        prompts the user when the actual command is dangerous.
        """
        return self.risk
