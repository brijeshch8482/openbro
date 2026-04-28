"""Base LLM provider interface."""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class Message:
    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_calls: list = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class LLMResponse:
    content: str
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""


class LLMProvider(ABC):
    """Base class for all LLM providers."""

    @abstractmethod
    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        ...

    def stream(self, messages: list[Message], tools: list[dict] | None = None) -> Iterator[str]:
        """Stream response tokens. Default falls back to non-streaming."""
        response = self.chat(messages, tools)
        yield response.content

    @abstractmethod
    def supports_tools(self) -> bool:
        ...

    @abstractmethod
    def name(self) -> str:
        ...
