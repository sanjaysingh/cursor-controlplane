"""CLI entry: HTTP server and `configure` subcommands."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import sys

import uvicorn

from control_plane.app import create_app
from control_plane.config import get_settings
from control_plane.db import Database
from control_plane.paths import database_path
from control_plane.setup_wizard import (
    needs_interactive_setup,
    run_setup_wizard,
    set_telegram_allowlist_cli,
    set_telegram_token_cli,
    show_config_cli,
)


async def _maybe_first_run_wizard() -> None:
    if not needs_interactive_setup():
        return
    db = Database(database_path())
    await db.init_schema()
    await run_setup_wizard(db, force=False)


def _handle_configure(rest: list[str]) -> None:
    if not rest or rest[0] == "wizard":
        asyncio.run(_configure_wizard())
        return
    if rest[0] == "show":
        asyncio.run(show_config_cli())
        return
    if rest[0] == "telegram-token" and len(rest) >= 2:
        asyncio.run(set_telegram_token_cli(rest[1]))
        return
    if rest[0] == "telegram-allowlist" and len(rest) >= 2:
        ids = ",".join(rest[1:])
        asyncio.run(set_telegram_allowlist_cli(ids))
        return
    print(
        "Usage: configure [wizard|show|telegram-token TOKEN|telegram-allowlist IDS...]",
        file=sys.stderr,
    )
    raise SystemExit(2)


async def _configure_wizard() -> None:
    db = Database(database_path())
    await db.init_schema()
    await run_setup_wizard(db, force=True)


_COMMAND_ALIASES = {"config": "configure"}
_PRIMARY_COMMANDS = frozenset({"serve", "configure", "restart"})


def _resolve_command(raw: str) -> str:
    """Map aliases and validate; typo hints for strings like 'configre' -> configure."""
    c = raw.strip().lower()
    if c in _COMMAND_ALIASES:
        return _COMMAND_ALIASES[c]
    if c in _PRIMARY_COMMANDS:
        return c
    pool = sorted(_PRIMARY_COMMANDS | set(_COMMAND_ALIASES.keys()))
    matches = difflib.get_close_matches(c, pool, n=1, cutoff=0.55)
    if matches:
        guess = _COMMAND_ALIASES.get(matches[0], matches[0])
        print(
            f"Unknown command {raw!r}. Did you mean {guess!r}?",
            file=sys.stderr,
        )
    else:
        print(
            f"Unknown command {raw!r}. Choose: serve | configure (alias: config) | restart",
            file=sys.stderr,
        )
    raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cursor CLI Control Plane - web + Telegram to agent (ACP).",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        metavar="COMMAND",
        help="serve | configure (alias: config) | restart",
    )
    parser.add_argument(
        "configure_args",
        nargs="*",
        metavar="SUBCOMMAND",
        help="configure: show | wizard | telegram-token TOKEN | telegram-allowlist IDS",
    )
    args = parser.parse_args()
    command = _resolve_command(args.command)
    if command == "restart":
        from control_plane.service_control import restart_service

        raise SystemExit(restart_service())
    if command == "configure":
        _handle_configure(list(args.configure_args))
        return

    asyncio.run(_maybe_first_run_wizard())
    app_config, _env = get_settings()
    uvicorn.run(
        create_app,
        factory=True,
        host=app_config.server.host,
        port=app_config.server.port,
        log_level="info",
    )
