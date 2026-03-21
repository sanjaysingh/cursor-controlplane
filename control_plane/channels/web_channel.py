"""Web dashboard channel: events via EventHub; answers via HTTP API."""

from __future__ import annotations

import asyncio
import logging

from control_plane.channels.base import BaseChannel
from control_plane.events import EventHub
from control_plane.models import MessageTarget

logger = logging.getLogger(__name__)


def _pending_key(session_id: str, conversation_id: str) -> str:
    return f"{session_id}:{conversation_id}"


class WebChannel(BaseChannel):
    name = "web"

    def __init__(self, hub: EventHub) -> None:
        self.hub = hub
        # One pending question per (session_id, web channel_key / conversation_id).
        self._pending: dict[str, asyncio.Future[str]] = {}

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def send_message(self, conversation_id: str, text: str) -> None:
        await self.hub.publish(
            {
                "type": "channel_message",
                "channel": self.name,
                "conversation_id": conversation_id,
                "text": text,
            }
        )

    async def ask_question(
        self,
        conversation_id: str,
        question: str,
        options: list[str],
        target: MessageTarget,
    ) -> str:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        sid = target.session_id
        pk = _pending_key(sid, conversation_id)
        self._pending[pk] = fut
        await self.hub.publish(
            {
                "type": "question",
                "channel": self.name,
                "conversation_id": conversation_id,
                "session_id": sid,
                "question": question,
                "options": options,
            }
        )
        try:
            return await asyncio.wait_for(fut, timeout=3600.0)
        except asyncio.TimeoutError:
            return options[0] if options else ""
        finally:
            self._pending.pop(pk, None)

    def submit_answer(self, session_id: str, answer: str, conversation_id: str | None = None) -> bool:
        if conversation_id is not None:
            pk = _pending_key(session_id, conversation_id)
            fut = self._pending.get(pk)
            if fut and not fut.done():
                fut.set_result(answer)
                return True
            return False
        for pk, fut in list(self._pending.items()):
            if pk.startswith(f"{session_id}:") and not fut.done():
                fut.set_result(answer)
                return True
        return False

    def cancel_pending_question(self, session_id: str) -> None:
        for pk, fut in list(self._pending.items()):
            if pk.startswith(f"{session_id}:") and not fut.done():
                fut.set_result("")
