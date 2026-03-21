"""Simple pub/sub for WebSocket + internal subscribers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

Subscriber = Callable[[dict[str, Any]], Awaitable[None]]


class EventHub:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = asyncio.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        self._subs.append(fn)

    def unsubscribe(self, fn: Subscriber) -> None:
        if fn in self._subs:
            self._subs.remove(fn)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                await fn(event)
            except Exception:
                import logging

                logging.getLogger(__name__).exception("Subscriber failed")
