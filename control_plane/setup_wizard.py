"""Interactive first-run and `configure` CLI for storing settings in SQLite."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from control_plane.config import (
    SETTING_ACP_COMMAND,
    SETTING_ACP_DEFAULT_MODEL,
    SETTING_CURSOR_API_KEY,
    SETTING_SERVER_HOST,
    SETTING_SERVER_PORT,
    SETTING_SETUP_WIZARD_COMPLETED,
    SETTING_TELEGRAM_ALLOWED_USER_IDS,
    SETTING_TELEGRAM_BOT_TOKEN,
    SETTING_TELEGRAM_ENABLED,
    SETTING_WEB_CHANNEL_ENABLED,
    AppConfig,
    get_settings,
    load_db_overrides,
    load_yaml_config,
)
from control_plane.constants import WEB_CHANNEL_KEY
from control_plane.db import Database
from control_plane.paths import (
    database_path,
    default_data_dir,
    is_frozen,
    service_marker_path,
    static_package_dir,
    user_shared_data_dir,
)
from control_plane.workspace_paths import resolve_workspace_root


def _maybe_tip_restart() -> None:
    if service_marker_path().is_file():
        print(
            "Background service install detected; apply DB changes with: cursor-controlplane restart",
            file=sys.stderr,
        )


def _truthy_setting(raw: str | None) -> bool:
    if raw is None or not (raw := raw.strip()):
        return False
    return raw.lower() in ("true", "1", "yes", "on")


def needs_interactive_setup(db_path: Path | None = None) -> bool:
    """True when Telegram is enabled, setup not finished, no token from .env/DB, and stdin is a TTY."""
    if not sys.stdin.isatty():
        return False
    yaml_cfg = load_yaml_config()
    if not yaml_cfg.channels.telegram.get("enabled", True):
        return False
    path = db_path or database_path()
    overrides = load_db_overrides(path)
    if _truthy_setting(overrides.get(SETTING_SETUP_WIZARD_COMPLETED)):
        return False
    # Use merged settings so .env (pydantic env_file) and DB overrides match runtime.
    _, env = get_settings()
    if (env.telegram_bot_token or "").strip():
        return False
    return True


def _prompt(msg: str, default: str = "") -> str:
    if default:
        line = input(f"{msg} [{default}]: ").strip()
        return line if line else default
    return input(f"{msg}: ").strip()


def _prompt_yes_no(msg: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{msg} ({hint}): ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true", "on")


async def run_setup_wizard(db: Database, *, force: bool = False) -> None:
    """Prompt for Telegram and basic options; persist to app_settings."""
    yaml_cfg = load_yaml_config()
    if not force and not yaml_cfg.channels.telegram.get("enabled", True):
        print("Telegram is disabled in config.yaml; skipping Telegram setup.", file=sys.stderr)
        return

    print("\n=== Cursor Control Plane — setup ===\n", file=sys.stderr)
    print(
        "Enter your Telegram bot token (from @BotFather). "
        "Leave empty to skip only if you will set TELEGRAM_BOT_TOKEN in the environment.\n",
        file=sys.stderr,
    )
    token = _prompt("Telegram bot token", "")
    if not token.strip():
        print(
            "No token saved. Set TELEGRAM_BOT_TOKEN or run: python run.py configure telegram-token <TOKEN>",
            file=sys.stderr,
        )
    else:
        await db.set_setting(SETTING_TELEGRAM_BOT_TOKEN, token.strip())

    allow = _prompt(
        "Allowed Telegram user IDs (comma-separated; only these users can use the bot)",
        "",
    )
    if allow.strip():
        await db.set_setting(SETTING_TELEGRAM_ALLOWED_USER_IDS, allow.strip())

    tg_on = _prompt_yes_no("Enable Telegram channel", default=True)
    await db.set_setting(SETTING_TELEGRAM_ENABLED, "true" if tg_on else "false")

    web_on = _prompt_yes_no("Enable web dashboard channel", default=True)
    await db.set_setting(SETTING_WEB_CHANNEL_ENABLED, "true" if web_on else "false")

    host = _prompt("HTTP bind host (empty = keep config/env default)", "")
    if host:
        await db.set_setting(SETTING_SERVER_HOST, host)

    port_raw = _prompt("HTTP port (empty = keep config/env default)", "")
    if port_raw.strip():
        await db.set_setting(SETTING_SERVER_PORT, port_raw.strip())

    acp_cmd = _prompt("ACP command (empty = default `agent`)", "")
    if acp_cmd.strip():
        await db.set_setting(SETTING_ACP_COMMAND, acp_cmd.strip())

    model = _prompt("Default ACP model id (empty = auto / omit flag)", "")
    if model.strip():
        await db.set_setting(SETTING_ACP_DEFAULT_MODEL, model.strip())

    await db.set_setting(SETTING_SETUP_WIZARD_COMPLETED, "true")

    print(
        "\nSetup saved to the local database. Start the server with "
        "`python run.py` or `cursor-controlplane` (release binary).\n",
        file=sys.stderr,
    )
    _maybe_tip_restart()


def _or_not_set(val: str | None) -> str:
    """Unset/blank -> NOT_SET; otherwise show value as stored (no masking)."""
    if val is None:
        return "NOT_SET"
    if not str(val).strip():
        return "NOT_SET"
    return str(val)


def _session_default_model_label(overrides: dict[str, str], app: AppConfig) -> str:
    """Matches SessionManager: DB default_model key, then acp.default_model, else Auto."""
    db_dm = (overrides.get("default_model") or "").strip()
    if db_dm:
        return f"{db_dm} (app_settings.default_model)"
    cfg_dm = (app.acp.default_model or "").strip()
    if cfg_dm:
        return f"{cfg_dm} (acp.default_model)"
    return "Auto"


def print_resolved_config() -> None:
    """Print paths, env overrides, DB keys, and merged effective runtime values."""
    db_path = database_path()
    app_config, env = get_settings()
    overrides = load_db_overrides(db_path)
    dotenv_path = Path.cwd() / ".env"
    root_resolved = resolve_workspace_root(app_config, env)

    print("=== Paths ===")
    print(f"database: {db_path}")
    print(f"  exists: {db_path.is_file()}")
    print(f"default_data_dir: {default_data_dir()}")
    print(f"user_shared_data_dir: {user_shared_data_dir()}")
    print(f"static_package_dir: {static_package_dir()}")
    print(f"service_marker: {service_marker_path()}")
    print(f"  exists: {service_marker_path().is_file()}")
    print(f"frozen_binary: {is_frozen()}")
    print(f"cwd: {os.getcwd()}")
    print(f".env (pydantic loads if present): {dotenv_path} exists={dotenv_path.is_file()}")

    print("\n=== Environment variables (shell / service) ===")
    for key in (
        "CONTROL_PLANE_CONFIG",
        "CONTROL_PLANE_DB_PATH",
        "CONTROL_PLANE_DATA_DIR",
        "CONTROL_PLANE_SERVICE_MARKER",
    ):
        v = os.environ.get(key)
        print(f"{key}={v if v else '(unset)'}")
    wr = os.environ.get("CONTROL_PLANE_WORKSPACE_ROOT")
    print(f"CONTROL_PLANE_WORKSPACE_ROOT={wr if wr else '(unset)'}")

    print("\n=== SQLite app_settings (stored overrides) ===")
    if not overrides:
        print("(no rows or DB file missing)")
    else:
        for key in sorted(overrides.keys()):
            raw = overrides[key]
            if key in (SETTING_TELEGRAM_BOT_TOKEN, SETTING_CURSOR_API_KEY):
                disp = _or_not_set(raw)
            else:
                disp = raw if (raw or "").strip() else "NOT_SET"
            print(f"{key}={disp}")

    print("\n=== Resolved runtime (effective; DB overrides win over .env and config.yaml) ===")
    print(f"workspace_root_env: {_or_not_set(env.workspace_root)}")
    print(f"workspace_root_config_yaml: {_or_not_set(app_config.workspace_root)}")
    print(f"workspace_root_resolved: {root_resolved}")
    print(
        f"http_listen: {app_config.server.host}:{app_config.server.port} "
        f"(bind + port used by the server)"
    )
    print(f"telegram_channel_enabled: {app_config.channels.telegram.get('enabled', True)}")
    print(f"web_channel_enabled: {app_config.channels.web.get('enabled', True)}")
    print(f"web_channel_key: {WEB_CHANNEL_KEY} (fixed)")
    print(f"acp.command: {app_config.acp.command}")
    dm = (app_config.acp.default_model or "").strip()
    print(f"acp.default_model: {dm if dm else 'Auto'}")
    print(f"acp.stream_update_mode: {app_config.acp.stream_update_mode}")
    print(f"session_default_model: {_session_default_model_label(overrides, app_config)}")
    print("--- credentials / env (merged) ---")
    print(f"cursor_api_key={_or_not_set(env.cursor_api_key)}")
    print(f"telegram_bot_token={_or_not_set(env.telegram_bot_token)}")
    print(f"telegram_allowed_user_ids={_or_not_set(env.telegram_allowed_user_ids)}")


async def show_config_cli() -> None:
    """Print resolved paths and merged configuration."""
    print_resolved_config()


async def set_telegram_token_cli(token: str) -> None:
    db = Database(database_path())
    await db.init_schema()
    await db.set_setting(SETTING_TELEGRAM_BOT_TOKEN, token.strip())
    print("telegram_bot_token saved.", file=sys.stderr)
    _maybe_tip_restart()


async def set_telegram_allowlist_cli(raw: str) -> None:
    db = Database(database_path())
    await db.init_schema()
    await db.set_setting(SETTING_TELEGRAM_ALLOWED_USER_IDS, raw.strip())
    print("telegram_allowed_user_ids saved.", file=sys.stderr)
    _maybe_tip_restart()
