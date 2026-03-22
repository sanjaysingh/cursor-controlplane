"""Shared fixtures: isolated SQLite DB and Telegram disabled."""

from __future__ import annotations

import textwrap

import pytest
from fastapi.testclient import TestClient


MINIMAL_CONFIG = textwrap.dedent(
    """\
    repos: []
    channels:
      telegram:
        enabled: false
      web:
        enabled: true
    """
)


@pytest.fixture
def test_config_path(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return p


@pytest.fixture
def test_db_path(tmp_path):
    return tmp_path / "test_control_plane.db"


@pytest.fixture
def client(monkeypatch, test_config_path, test_db_path):
    monkeypatch.setenv("CONTROL_PLANE_CONFIG", str(test_config_path))
    monkeypatch.setenv("CONTROL_PLANE_DB_PATH", str(test_db_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    # Import after env is set so create_app() picks up paths.
    from control_plane.app import create_app

    app = create_app()
    with TestClient(app) as tc:
        yield tc
