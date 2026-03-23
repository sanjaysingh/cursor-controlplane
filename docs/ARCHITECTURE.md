# Architecture

## Overview

- **Channels** (`control_plane/channels/`): pluggable I/O — `telegram`, `web` (EventHub + HTTP answer endpoint).
- **SessionManager** (`session_manager.py`): **conversational agent sessions**. Each session has a fixed **workspace** (`repo_path`), one **long-lived** `agent acp` process until the user **explicitly closes** the session. Prompts are sent with `session_prompt` on the same ACP client. SQLite stores session metadata and chat history.
- **AcpClient** (`acp_client.py`): subprocess `agent … acp` (optional `--model` from session / env / `acp.default_model`), JSON-RPC over NDJSON stdin/stdout; handles `session/update`, `session/request_permission`, `cursor/*` extensions; `session_new`, `session_load`, `session_prompt`. After `session/new` (and after `session/load`), calls **`session/set_config_option`** for the model when possible — Cursor ACP may ignore CLI `--model` but honor ACP session config.
- **EventHub** (`events.py`): pub/sub for WebSocket broadcast (`session_updated`, `session_closed`, `session_removed`, `agent_stream`, `question`, `sessions_purged`).
- **Database** (`db.py`): SQLite — `agent_sessions`, `session_messages` (plus legacy `conversations` / `messages` if present from older DBs).
- **agent_models** (`agent_models.py`): runs `agent --list-models` / `agent models` for **`GET /api/models`** — **web dashboard model dropdown** (each row is the exact `agent --model` string; **Auto** = null / omit flag).
- **acp_model_probe** (`acp_model_probe.py`): optional **`GET /api/models/acp`** (ACP `session/new` probe + diagnostics); not used for the main picker.
- **model_cli** (`model_cli.py`): normalizes list lines like `model-id - Label` to the real `agent --model` id (stored/sent value).
- **workspace_paths** (`workspace_paths.py`): resolves **`workspace_root`** (config / env / default `~/cursor-control-plane`) and lists top-level workspace folders.
- **github_cli** (`github_cli.py`): `gh repo list` / `gh repo clone` into the workspace root.
- **repo_picker** (`repo_picker.py`): builds deduped options for **`GET /api/repo-picker`** (web repository dropdown).

## Agent sessions

Each session has a `session_id` (UUID), `channel` + `channel_key` (e.g. web client id), **`repo_path`** (workspace), **`title`** (optional on create; default is the workspace folder basename), optional **`model`** (exact CLI id for `agent --model`, or **null** for Auto / then DB `default_model` preference / `acp.default_model` if set), and `status`: **`open`** | **`closed`**.

- **While open**: a single **AcpClient** is kept for that session; multiple user messages reuse the same CLI/ACP connection.
- **Close** (`POST /api/sessions/{id}/close`, legacy `POST /api/runs/{id}/stop`, Telegram `/session_close`): cancel pending questions, kill subprocess, **delete** the session row and messages from SQLite, emit **`session_removed`**, clear in-memory handle. **Close all** (`POST /api/sessions/close-all`) deletes every session (same as the former purge-all). At most **5** sessions may exist; creating another requires closing one first.
- **Legacy DB rows** with `status = closed` from older builds may still exist until removed; the web list defaults to **open** sessions only. **`send_session_message`** can still **reopen** a legacy closed row if present.

**Activity** (ephemeral, for UI): `idle` | `connecting` | `running` | `waiting_user` | `error`.

**Streaming**: `acp.stream_update_mode` defaults to **`agent_message_chunk_only`** — only `session/update` with `sessionUpdate === "agent_message_chunk"` contributes to the dashboard stream and stored assistant text (matches [Cursor ACP minimal client](https://cursor.com/docs/cli/acp)). Set to **`all`** to restore the previous broad text extraction (may include reasoning-style chunks).

## Telegram vs web

- **Telegram**: one **open** session per `(channel, chat_id, repo_path)`; plain text continues that session. **`/session_new`** creates a session with **empty `repo_path`** (agent cwd = **workspace root**, same as web “No repository”). **`/repos`** (GitHub CLI) and **`/workspaces`** (folders under the workspace root) set the workspace and start a fresh session on the next message; **`/session_close`** stops the agent; **`/sessions`** connects to an existing session.
- **Web**: `channel_key` from dashboard (localStorage) scopes the session list; user picks any session (open or closed) and continues the thread in the chat pane. **New session** uses **`GET /api/repo-picker`** for a unified repository dropdown (locals + GitHub, deduped); **`POST /api/github/clone`** runs when a not-yet-cloned GitHub repo is selected.

## Extending channels

Implement `BaseChannel` (`channels/base.py`), register in `ChannelRegistry`, start/stop from `app.py` lifespan. Questions should use `MessageTarget(session_id=..., conversation_id=...)` so answers route to the correct session.

## Configuration

- `config.yaml`: repos, optional **`workspace_root`** (absolute or `~`; default **`~/cursor-control-plane`**), server bind, `acp.command` (default `agent`), `acp.default_model`, `acp.stream_update_mode`, `acp.extra_args`.
- `.env`: `CURSOR_API_KEY`, `TELEGRAM_BOT_TOKEN`, optional **`CONTROL_PLANE_WORKSPACE_ROOT`** (overrides `workspace_root` in YAML). Web dashboard participant id is fixed in code (`WEB_CHANNEL_KEY` in `control_plane/constants.py`).
- **SQLite `app_settings`**: optional keys (Telegram token, allowlist, server host/port, `acp.command`, etc.) **override** matching env and YAML values when non-empty. See `SETTING_*` constants in `control_plane/config.py`. Interactive setup (`python run.py configure` / first-run wizard) writes here.
- **Paths**: default DB is `data/control_plane.db` in dev; PyInstaller / release builds use a per-user data directory (see README). Override with **`CONTROL_PLANE_DB_PATH`** or **`CONTROL_PLANE_DATA_DIR`**.
- **GitHub**: listing/cloning uses the **`gh`** CLI (`gh repo list`, `gh repo clone`); install `gh` and run `gh auth login` on the host running the control plane.
