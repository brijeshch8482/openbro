"""Base tool interface."""

from abc import ABC, abstractmethod


class BaseTool(ABC):
    name: str = ""
    description: str = ""

    @abstractmethod
    def run(self, **kwargs) -> str:
        ...

    @abstractmethod
    def schema(self) -> dict:
        ...
