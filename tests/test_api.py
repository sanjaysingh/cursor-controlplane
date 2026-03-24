"""HTTP API: health, dashboard config, sessions, WebSocket ping/pong."""

from __future__ import annotations

import pytest

from control_plane import __version__


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}


def test_dashboard_config_includes_keys(client):
    r = client.get("/api/dashboard-config")
    assert r.status_code == 200
    data = r.json()
    assert "web_channel_key" in data
    assert "workspace_root" in data
    assert "default_model" in data


def test_put_default_model_round_trips(client):
    r = client.put(
        "/api/settings/default-model",
        json={"model": "test-model-id"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("default_model") == "test-model-id"

    cfg = client.get("/api/dashboard-config").json()
    assert cfg.get("default_model") == "test-model-id"

    client.put("/api/settings/default-model", json={"model": None})
    cleared = client.get("/api/dashboard-config").json()
    assert cleared.get("default_model") == ""


def test_create_session_rejects_invalid_repo_path(client):
    r = client.post(
        "/api/sessions",
        json={"repo_path": "/path/does/not/exist/ever"},
    )
    assert r.status_code == 400
    assert "directory" in r.json().get("error", "").lower()


def test_create_list_close_session_empty_repo(client, tmp_path):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    sess = r.json()
    sid = sess["id"]
    assert sess.get("repo_path") == ""

    listed = client.get("/api/sessions").json()
    assert any(s["id"] == sid for s in listed)

    close = client.post(f"/api/sessions/{sid}/close")
    assert close.status_code == 200
    assert close.json().get("ok") is True


def test_post_message_empty_text_returns_400(client):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    sid = r.json()["id"]
    bad = client.post(
        f"/api/sessions/{sid}/message",
        json={"text": "   "},
    )
    assert bad.status_code == 400
    client.post(f"/api/sessions/{sid}/close")


def test_session_limit_enforced(client):
    ids: list[str] = []
    try:
        for _ in range(5):
            r = client.post("/api/sessions", json={})
            assert r.status_code == 200, r.text
            ids.append(r.json()["id"])
        sixth = client.post("/api/sessions", json={})
        assert sixth.status_code == 400
        assert "Maximum" in sixth.json().get("error", "")
    finally:
        client.post("/api/sessions/close-all")


def test_websocket_hello_and_pong(client):
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello.get("type") == "hello"
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong.get("type") == "pong"


def test_repo_picker_clamps_gh_limit(client):
    r = client.get("/api/repo-picker?gh_limit=500")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert isinstance(data["items"], list)
