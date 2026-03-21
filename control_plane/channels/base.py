"""Channel plugin interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from control_plane.models import MessageTarget


class BaseChannel(ABC):
    """All I/O channels implement this interface."""

    name: str = "base"

    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...

    @abstractmethod
    async def send_message(self, conversation_id: str, text: str) -> None:
        """Send plain text to the user in this channel."""

    @abstractmethod
    async def ask_question(
        self,
        conversation_id: str,
        question: str,
        options: list[str],
        target: MessageTarget,
    ) -> str:
        """Block until the user picks an option or types an answer; return chosen text."""
