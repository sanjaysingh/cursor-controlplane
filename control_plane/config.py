"""Load YAML config and environment."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def parse_telegram_allowed_user_ids(raw: str) -> frozenset[int]:
    """Parse TELEGRAM_ALLOWED_USER_IDS: comma- or whitespace-separated integers."""
    if not (raw or "").strip():
        return frozenset()
    out: set[int] = set()
    for part in re.split(r"[\s,]+", raw.strip()):
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            logger.warning("Ignoring invalid Telegram user id in TELEGRAM_ALLOWED_USER_IDS: %r", part)
    return frozenset(out)


class RepoEntry(BaseModel):
    name: str
    path: str
    description: str = ""


class ChannelsConfig(BaseModel):
    telegram: dict[str, Any] = Field(default_factory=lambda: {"enabled": True})
    web: dict[str, Any] = Field(default_factory=lambda: {"enabled": True})


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class AcpConfig(BaseModel):
    command: str = "agent"
    extra_args: list[str] = Field(default_factory=list)
    default_model: str = Field(
        default="",
        description="When session model is Auto (null): optional `agent --model`; empty = omit flag. Run `agent models` for ids.",
    )
    stream_update_mode: Literal["agent_message_chunk_only", "all"] = Field(
        default="agent_message_chunk_only",
        description=(
            "session/update handling: agent_message_chunk_only matches Cursor ACP docs; "
            "'all' keeps legacy broad text extraction."
        ),
    )


class AppConfig(BaseModel):
    repos: list[RepoEntry] = Field(default_factory=list)
    workspace_root: str = Field(
        default="",
        description="Absolute or ~ path; empty uses default ~/cursor-control-plane at runtime.",
    )
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    acp: AcpConfig = Field(default_factory=AcpConfig)


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cursor_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = Field(
        default="",
        description="Comma-separated Telegram user IDs allowed to use the bot (required for access).",
    )
    cursor_agent_model: str = Field(
        default="",
        description="Optional default model (overrides acp.default_model; session.model still wins).",
    )
    web_channel_key: str = Field(
        default="web:default",
        description=(
            "Fixed (channel, conversation_id) for the web dashboard until auth exists. "
            "Override via env WEB_CHANNEL_KEY for multi-profile on one server."
        ),
    )
    workspace_root: str = Field(
        default="",
        validation_alias=AliasChoices("workspace_root", "CONTROL_PLANE_WORKSPACE_ROOT"),
        description="Override workspace root; default ~/cursor-control-plane when unset.",
    )


def load_yaml_config(path: Path | None = None) -> AppConfig:
    if path is None:
        path = Path(os.environ.get("CONTROL_PLANE_CONFIG", "config.yaml"))
    if not path.is_file():
        return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def get_settings() -> tuple[AppConfig, EnvSettings]:
    return load_yaml_config(), EnvSettings()
