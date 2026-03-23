"""First-run wizard gating: .env + DB flag."""

from __future__ import annotations

import sqlite3

import pytest

from control_plane.config import SETTING_SETUP_WIZARD_COMPLETED

TELEGRAM_ON_YAML = """\
repos: []
channels:
  telegram:
    enabled: true
  web:
    enabled: true
"""


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """config.yaml with Telegram on; cwd is tmp so project .env is not loaded."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(TELEGRAM_ON_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONTROL_PLANE_CONFIG", str(cfg.resolve()))
    return cfg


def test_needs_setup_false_when_token_in_env(monkeypatch, isolated_config, test_db_path):
    monkeypatch.setenv("CONTROL_PLANE_DB_PATH", str(test_db_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    from control_plane.setup_wizard import needs_interactive_setup

    assert needs_interactive_setup() is False


def test_needs_setup_false_when_completed_in_db(monkeypatch, isolated_config, test_db_path):
    monkeypatch.setenv("CONTROL_PLANE_DB_PATH", str(test_db_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    test_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(test_db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        (SETTING_SETUP_WIZARD_COMPLETED, "true"),
    )
    conn.commit()
    conn.close()

    from control_plane.setup_wizard import needs_interactive_setup

    assert needs_interactive_setup() is False


def test_needs_setup_true_when_telegram_on_no_token_no_flag(monkeypatch, isolated_config, test_db_path):
    monkeypatch.setenv("CONTROL_PLANE_DB_PATH", str(test_db_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    from control_plane.setup_wizard import needs_interactive_setup

    assert needs_interactive_setup() is True


def test_needs_setup_false_when_not_tty(monkeypatch, isolated_config, test_db_path):
    monkeypatch.setenv("CONTROL_PLANE_DB_PATH", str(test_db_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    from control_plane.setup_wizard import needs_interactive_setup

    assert needs_interactive_setup() is False
