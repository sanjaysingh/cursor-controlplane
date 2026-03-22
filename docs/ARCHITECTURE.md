# Architecture

## Overview

- **Channels** (`control_plane/channels/`): pluggable I/O — `telegram`, `web` (EventHub + HTTP answer endpoint).
- **SessionManager** (`session_manager.py`): **conversational agent sessions**. Each session has a fixed **workspace** (`repo_path`), one **long-lived** `agent acp` process until the user **explicitly closes** the session. Prompts are sent with `session_prompt` on the same ACP client. SQLite stores session metadata and chat history.
- **AcpClient** (`acp_client.py`): subprocess `agent … acp` (optional `--model` from session / env / `acp.default_model`), JSON-RPC over NDJSON stdin/stdout; handles `session/update`, `session/request_permission`, `cursor/*` extensions; `session_new`, `session_load`, `session_prompt`. After `session/new` (and after `session/load`), calls **`session/set_config_option`** for the model when possible — Cursor ACP may ignore CLI `--model` but honor ACP session config.
- **EventHub** (`events.py`): pub/sub for WebSocket broadcast (`session_updated`, `session_closed`, `agent_stream`, `question`).
- **Database** (`db.py`): SQLite — `agent_sessions`, `session_messages` (plus legacy `conversations` / `messages` if present from older DBs).
- **agent_models** (`agent_models.py`): runs `agent --list-models` / `agent models` for **`GET /api/models`** — **web dashboard model dropdown** (each row is the exact `agent --model` string; **Auto** = null / omit flag).
- **acp_model_probe** (`acp_model_probe.py`): optional **`GET /api/models/acp`** (ACP `session/new` probe + diagnostics); not used for the main picker.
- **model_cli** (`model_cli.py`): normalizes list lines like `model-id - Label` to the real `agent --model` id (stored/sent value).
- **workspace_paths** (`workspace_paths.py`): resolves **`workspace_root`** (config / env / default `~/cursor-control-plane`) and lists top-level workspace folders.
- **github_cli** (`github_cli.py`): `gh repo list` / `gh repo clone` into the workspace root.
- **repo_picker** (`repo_picker.py`): builds deduped options for **`GET /api/repo-picker`** (web repository dropdown).

## Agent sessions

Each session has a `session_id` (UUID), `channel` + `channel_key` (e.g. web client id), **`repo_path`** (workspace), **`title`** (optional on create; default is the workspace folder basename), optional **`model`** (exact CLI id for `agent --model`, or **null** for Auto / then env `CURSOR_AGENT_MODEL` / `acp.default_model` if set), and `status`: **`open`** | **`closed`**.

- **While open**: a single **AcpClient** is kept for that session; multiple user messages reuse the same CLI/ACP connection.
- **Close** (`POST /api/sessions/{id}/close`, legacy `POST /api/runs/{id}/stop`, Telegram `/session_close`): cancel pending questions, kill subprocess, mark row closed, remove in-memory handle.
- **Resume a closed session**: selecting it in the dashboard and sending a message (or Telegram flow creating/reusing sessions) calls `send_session_message`, which **reopens** the row, starts ACP again, and attempts **`session_load`** with the stored `acp_session_id` so context can be restored when the agent supports it.

**Activity** (ephemeral, for UI): `idle` | `connecting` | `running` | `waiting_user` | `error`.

**Streaming**: `acp.stream_update_mode` defaults to **`agent_message_chunk_only`** — only `session/update` with `sessionUpdate === "agent_message_chunk"` contributes to the dashboard stream and stored assistant text (matches [Cursor ACP minimal client](https://cursor.com/docs/cli/acp)). Set to **`all`** to restore the previous broad text extraction (may include reasoning-style chunks).

## Telegram vs web

- **Telegram**: one **open** session per `(channel, chat_id, repo_path)`; plain text continues that session. **`/repos`** (GitHub CLI) and **`/workspaces`** (folders under the workspace root) set the workspace and start a fresh session on the next message; **`/session_close`** stops the agent; **`/sessions`** connects to an existing session.
- **Web**: `channel_key` from dashboard (localStorage) scopes the session list; user picks any session (open or closed) and continues the thread in the chat pane. **New session** uses **`GET /api/repo-picker`** for a unified repository dropdown (locals + GitHub, deduped); **`POST /api/github/clone`** runs when a not-yet-cloned GitHub repo is selected.

## Extending channels

Implement `BaseChannel` (`channels/base.py`), register in `ChannelRegistry`, start/stop from `app.py` lifespan. Questions should use `MessageTarget(session_id=..., conversation_id=...)` so answers route to the correct session.

## Configuration

- `config.yaml`: repos, optional **`workspace_root`** (absolute or `~`; default **`~/cursor-control-plane`**), server bind, `acp.command` (default `agent`), `acp.default_model`, `acp.stream_update_mode`, `acp.extra_args`.
- `.env`: `CURSOR_API_KEY`, `TELEGRAM_BOT_TOKEN`, optional `CURSOR_AGENT_BIN`, optional `CURSOR_AGENT_MODEL`, optional **`CONTROL_PLANE_WORKSPACE_ROOT`** (overrides `workspace_root` in YAML).
- **GitHub**: listing/cloning uses the **`gh`** CLI (`gh repo list`, `gh repo clone`); install `gh` and run `gh auth login` on the host running the control plane.
