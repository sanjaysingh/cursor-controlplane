"""Application state container."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from control_plane.config import AppConfig, EnvSettings
from control_plane.db import Database
from control_plane.events import EventHub
from control_plane.channels.registry import ChannelRegistry
from control_plane.session_manager import SessionManager


@dataclass
class AppState:
    config: AppConfig
    env: EnvSettings
    db: Database
    hub: EventHub
    registry: ChannelRegistry
    session_manager: SessionManager
    static_dir: Path
