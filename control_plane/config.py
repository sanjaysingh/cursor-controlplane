"""Load YAML config and environment."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Keys stored in SQLite app_settings (highest priority over env + YAML when non-empty).
SETTING_TELEGRAM_BOT_TOKEN = "telegram_bot_token"
SETTING_TELEGRAM_ALLOWED_USER_IDS = "telegram_allowed_user_ids"
SETTING_TELEGRAM_ENABLED = "telegram_enabled"
SETTING_WEB_CHANNEL_ENABLED = "web_channel_enabled"
SETTING_SERVER_HOST = "server_host"
SETTING_SERVER_PORT = "server_port"
SETTING_ACP_COMMAND = "acp_command"
SETTING_ACP_DEFAULT_MODEL = "acp_default_model"
SETTING_CURSOR_API_KEY = "cursor_api_key"
def load_db_overrides(db_path: Path) -> dict[str, str]:
    """Read all app_settings rows synchronously (used before async app startup)."""
    if not db_path.is_file():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
    except OSError:
        return {}
    try:
        cur = conn.execute("SELECT key, value FROM app_settings")
        out: dict[str, str] = {}
        for k, v in cur.fetchall():
            if k is not None:
                out[str(k)] = "" if v is None else str(v)
        return out
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


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
    workspace_root: str = Field(
        default="",
        validation_alias=AliasChoices("workspace_root", "CONTROL_PLANE_WORKSPACE_ROOT"),
        description="Override workspace root; default ~/cursor-control-plane when unset.",
    )


def _parse_bool(raw: str | None) -> bool | None:
    if raw is None or not (raw := raw.strip()):
        return None
    low = raw.lower()
    if low in ("true", "1", "yes", "on"):
        return True
    if low in ("false", "0", "no", "off"):
        return False
    return None


def merge_env_from_db(env: EnvSettings, overrides: dict[str, str]) -> None:
    """Apply DB overrides onto env (mutates env)."""
    if (t := (overrides.get(SETTING_TELEGRAM_BOT_TOKEN) or "").strip()):
        env.telegram_bot_token = t
    if (a := (overrides.get(SETTING_TELEGRAM_ALLOWED_USER_IDS) or "").strip()):
        env.telegram_allowed_user_ids = a
    if (k := (overrides.get(SETTING_CURSOR_API_KEY) or "").strip()):
        env.cursor_api_key = k


def merge_app_config_from_db(app: AppConfig, overrides: dict[str, str]) -> AppConfig:
    """Return a copy of app with DB overrides applied (non-empty values win)."""
    server = app.server.model_copy()
    if (h := (overrides.get(SETTING_SERVER_HOST) or "").strip()):
        server.host = h
    if (p := (overrides.get(SETTING_SERVER_PORT) or "").strip()):
        try:
            server.port = int(p)
        except ValueError:
            logger.warning("Invalid %s in DB: %r", SETTING_SERVER_PORT, p)

    acp = app.acp.model_copy()
    if (c := (overrides.get(SETTING_ACP_COMMAND) or "").strip()):
        acp.command = c
    if (dm := (overrides.get(SETTING_ACP_DEFAULT_MODEL) or "").strip()):
        acp.default_model = dm

    tg = dict(app.channels.telegram)
    web = dict(app.channels.web)
    b = _parse_bool(overrides.get(SETTING_TELEGRAM_ENABLED))
    if b is not None:
        tg["enabled"] = b
    b = _parse_bool(overrides.get(SETTING_WEB_CHANNEL_ENABLED))
    if b is not None:
        web["enabled"] = b

    return app.model_copy(
        update={
            "server": server,
            "acp": acp,
            "channels": ChannelsConfig(telegram=tg, web=web),
        }
    )


def resolved_config_yaml_path() -> Path:
    """Absolute path to the YAML file `load_yaml_config` uses (relative names use cwd)."""
    raw = (os.environ.get("CONTROL_PLANE_CONFIG") or "config.yaml").strip() or "config.yaml"
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()
    return p


def load_yaml_config(path: Path | None = None) -> AppConfig:
    if path is None:
        path = Path(os.environ.get("CONTROL_PLANE_CONFIG", "config.yaml"))
    if not path.is_file():
        return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def get_settings() -> tuple[AppConfig, EnvSettings]:
    """Load YAML + env, then apply SQLite app_settings (highest priority for stored keys)."""
    from control_plane.paths import database_path

    app_config = load_yaml_config()
    env = EnvSettings()
    overrides = load_db_overrides(database_path())
    merge_env_from_db(env, overrides)
    app_config = merge_app_config_from_db(app_config, overrides)
    return app_config, env
