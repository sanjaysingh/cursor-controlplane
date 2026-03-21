from __future__ import annotations

from control_plane.channels.base import BaseChannel


class ChannelRegistry:
    def __init__(self) -> None:
        self._channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.name] = channel

    def get(self, name: str) -> BaseChannel:
        if name not in self._channels:
            raise KeyError(f"Unknown channel: {name}")
        return self._channels[name]

    def all(self) -> dict[str, BaseChannel]:
        return dict(self._channels)
