"""Base channel interface - CLI, Telegram, future Voice etc."""

from abc import ABC, abstractmethod


class Channel(ABC):
    """A communication channel between user and OpenBro agent."""

    name: str = ""

    @abstractmethod
    def start(self) -> None:
        """Start listening for messages on this channel."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the channel."""
