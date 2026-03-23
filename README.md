# Cursor CLI Control Plane

Personal control plane that routes instructions from **Telegram** (and a **static web dashboard**) to the **Cursor CLI** over **ACP** (`agent acp`), with **SQLite-backed conversational sessions**, dashboard session list + chat, and **explicit session close** (stop/kill agent for that session).

## Prerequisites

- Python 3.11+
- [Cursor CLI](https://cursor.com/docs/cli) installed and on your **`PATH`** (on **Windows**, see the section below if `agent` works in a terminal but not when you start the server from the IDE)
- `CURSOR_API_KEY` or completed `agent login` on the same machine
- (Optional) Telegram bot token from [@BotFather](https://t.me/BotFather)
- (Optional) [GitHub CLI](https://cli.github.com/) (`gh`) for **`/repos`** / **`GET /api/github/repos`** — run `gh auth login` on the server host

## Setup

From the **repository root** (the folder that contains `run.py` and `config.yaml`):

```bash
python -m venv .venv
```

On some systems only **`python3`** is available (common on macOS/Linux); use `python3 -m venv .venv` and `python3 run.py` in that case.

Activate the virtual environment, then install dependencies:

| Environment | Command |
|-------------|---------|
| **macOS / Linux / WSL** (bash, zsh, etc.) | `source .venv/bin/activate` |
| **Windows — Command Prompt** | `.venv\Scripts\activate.bat` |
| **Windows — PowerShell** | `.\.venv\Scripts\Activate.ps1` (if execution policy blocks scripts, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`) |

```bash
pip install -r requirements.txt
```

Copy the env template and edit values:

| OS | Command |
|----|---------|
| **Windows** (cmd / PowerShell) | `copy .env.example .env` |
| **macOS / Linux / WSL** | `cp .env.example .env` |

Set at least `CURSOR_API_KEY` and (if you use Telegram) `TELEGRAM_BOT_TOKEN`. See `.env.example` for optional variables.

**Virtual environment:** If you **move or copy** this project to a new folder, **do not copy `.venv`**. It embeds absolute paths and will confuse `python` / `pip`. Stop any running server (`python run.py` or `python3 run.py`), close terminals that activated this env, delete the `.venv` folder, then run `python -m venv .venv` and `pip install -r requirements.txt` again (use **`python3` / `pip3`** everywhere if that is what your OS provides).

- **Windows:** If delete says “access denied”, end Python in Task Manager or reboot, then remove `.venv`.
- **macOS / Linux / WSL:** `rm -rf .venv`

Edit `config.yaml`:

- Add one or more `repos` entries (name + path), or rely on Telegram **`/workspaces`**, **`/repos`**, or the web sidebar. Optional **`workspace_root`** (default **`~/cursor-control-plane`**) or env **`CONTROL_PLANE_WORKSPACE_ROOT`**.
- Toggle `channels.telegram.enabled` / `channels.web.enabled`.
- Optional: `acp.default_model` (global `agent --model`), `acp.stream_update_mode` (`agent_message_chunk_only` vs `all` — see [ACP docs](https://cursor.com/docs/cli/acp)).

## Run

```bash
python run.py
```

### First-run setup (interactive)

If Telegram is enabled in `config.yaml`, no `TELEGRAM_BOT_TOKEN` is set in the environment, and the database has no stored token yet, the server prompts once on a **TTY** for bot token, allowlist, and basic options. Values are saved in SQLite (`app_settings`) and override env/YAML for those keys on the next start.

### Configure without starting the server

| Command | Purpose |
|--------|---------|
| `python run.py configure` or `python run.py configure wizard` | Full interactive wizard (writes DB) |
| `python run.py configure show` | Print paths, env overrides, DB `app_settings`, and **resolved runtime** (workspace root, HTTP bind, channels, ACP, fixed `web_channel_key`, `session_default_model`); `NOT_SET` when unset; secrets shown in full when set. Does not dump `config.yaml`. |
| `python run.py configure telegram-token <TOKEN>` | Store Telegram bot token |
| `python run.py configure telegram-allowlist <ids>` | Comma- or space-separated user IDs |
| `python run.py restart` | Restart the background service installed by `install.sh --with-service` / `install.ps1 -WithService` (reloads config from DB) |

After `pip install -e .`, the same commands work as `cursor-controlplane ...`.

**Priority:** stored settings in the DB override environment variables and `config.yaml` for the keys listed in [`control_plane/config.py`](control_plane/config.py) (e.g. Telegram token, allowlist, server host/port, `acp.command`).

### Release binaries (GitHub Actions)

Pushing a tag `v*` runs [`.github/workflows/release.yml`](.github/workflows/release.yml) and attaches PyInstaller **one-file** builds:

- `cursor-controlplane-linux-amd64`
- `cursor-controlplane-macos-arm64`
- `cursor-controlplane-windows-amd64.exe`

Local build (optional):

```bash
pip install -r requirements-build.txt
pyinstaller --noconfirm cursor-controlplane.spec
# dist/cursor-controlplane(.exe)
```

**Install from GitHub (examples)**

Set `CONTROL_PLANE_REPO` to your `owner/repo`, then:

| OS | Command |
|----|---------|
| Linux / macOS | `curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/install.sh \| bash` |
| Windows (PowerShell) | `irm https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/install.ps1 \| iex` |

Install scripts download the **latest** release asset and place the binary under `~/.local/bin` (Unix) or `%LOCALAPPDATA%\Programs\cursor-controlplane\` (Windows) and update user `PATH` where applicable.

**Background service (one command):** to install the binary **and** register an auto-starting user service, use:

| OS | Command |
|----|---------|
| Linux / macOS | `curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/install.sh \| bash -s -- --with-service` |
| Linux / macOS (env) | `CONTROL_PLANE_INSTALL_SERVICE=1 bash install.sh` (after setting `CONTROL_PLANE_REPO`) |
| Windows (PowerShell) | `$env:CONTROL_PLANE_INSTALL_SERVICE = "1"; irm ... \| iex` or download `install.ps1` and run `.\install.ps1 -WithService` |

- **Linux:** systemd **user** unit `cursor-controlplane.service` (`systemctl --user status …`). On a headless server, user units may need **loginctl enable-linger $USER** so the service runs without an interactive login.
- **macOS:** LaunchAgent `com.cursor.controlplane` in `~/Library/LaunchAgents/`.
- **Windows:** a **Scheduled Task** `CursorControlPlane` (runs at log on).

The install script writes `service.json` under the app data directory (see below). **`cursor-controlplane configure`** updates the **same** SQLite DB the service uses when you run the **installed** binary (not `python run.py` from a source checkout, which uses `./data/` by default). After changing configuration, run **`cursor-controlplane restart`** so the server reloads settings from the DB.

**Data directory:** bundled / binary installs use a per-user data dir (SQLite and state): Linux/macOS `~/.local/share/cursor-controlplane/` (or `$XDG_DATA_HOME/cursor-controlplane/`), Windows `%APPDATA%\cursor-controlplane\`. Override with **`CONTROL_PLANE_DATA_DIR`** or **`CONTROL_PLANE_DB_PATH`**. Service metadata is stored next to the DB as `service.json` (override path with **`CONTROL_PLANE_SERVICE_MARKER`** if needed).

**Uninstall (one script):** removes the **service** (if present), **binary**, and **data directory** (SQLite + `service.json`). Optional **`--keep-data`** / **`-KeepData`** keeps the DB folder.

| OS | Command |
|----|---------|
| Linux / macOS | `curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/uninstall.sh \| bash` — non-interactive: `\| bash -s -- -y` or `CONTROL_PLANE_UNINSTALL_YES=1` |
| Windows (PowerShell) | `irm https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/uninstall.ps1 \| iex` — or download and run `.\uninstall.ps1 -Yes` |

Stop the running app first if the uninstaller cannot delete the executable. **`CONTROL_PLANE_DATA_DIR`** matches install when removing data (same as install scripts).

## Tests

**Python (API, database, model parsing):** from the repo root, with dev dependencies installed:

| Step | Command |
|------|---------|
| Install | `pip install -r requirements.txt -r requirements-dev.txt` |
| Run | `pytest` |

Tests use a temporary SQLite file and config via `CONTROL_PLANE_DB_PATH` and `CONTROL_PLANE_CONFIG` (set by fixtures); they do not touch your normal `data/control_plane.db`.

**Dashboard helpers (shared `dashboard-utils.js`):**

| Step | Command |
|------|---------|
| Install | `npm install` |
| Run | `npm test` |

**CI:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs both suites on every push and pull request to `main`.

**Requiring tests on pull requests:** in the GitHub repo, go to **Settings → Rules → Rulesets** (or **Branches → Branch protection rules**), protect `main`, and enable **Require status checks to pass**. Add the **Tests** check from the **CI** workflow (exact label may appear as `Tests` or `CI / Tests` depending on GitHub’s UI). Until that rule exists, CI still runs on PRs but merging is not blocked automatically.

- Dashboard: `http://localhost:8080/` (adjust port in `config.yaml`; static assets are under `/assets/` so WebSocket `/ws` is not blocked). The UI uses **Tailwind CSS**, **Alpine.js**, **marked**, and **DOMPurify** (CDN) so chat messages render **Markdown** safely—no frontend build step.
- **Sessions API**: `GET/POST /api/sessions` (optional `model` on create only), `GET /api/sessions/{id}/messages`, `POST /api/sessions/{id}/message`, `POST /api/sessions/{id}/close`, `POST /api/sessions/{id}/answer`, **`WebSocket /ws`**
- **`GET /api/repo-picker`**: deduped **local** + **GitHub** entries for the New session dropdown · **`GET /api/workspaces`**, **`GET /api/github/repos`**, **`POST /api/github/clone`** (still available for integrations) · **`GET /api/dashboard-config`**: fixed `web_channel_key` (see `control_plane/constants.py`) + **`workspace_root`**
- **`GET /api/models`**: exact ids from `agent models` / `--list-models` — **dashboard dropdown** uses these strings as both label and value (same as `agent --model <id>`). First row **Auto** = omit `--model`.
- **`GET /api/models/acp?workspace=<dir>`**: optional ACP probe (diagnostics / advanced use); the web UI does not use it for the picker.
- **Legacy aliases** (same UUID as session id): `GET/POST /api/runs`, `POST /api/runs/{id}/stop`, `POST /api/runs/{id}/answer`

### Troubleshooting empty model dropdown

1. **Browser (F12 → Console)**: filter **`[cp-models]`** — should show `GET /api/models` and `modelCount`.
2. **Network tab**: open **`/api/models`** — JSON `models` array; `error` if the CLI failed.
3. **Server terminal**: `GET /models` log line; run `agent models` on the same machine if the list is empty.

## How sessions work

- **Workspace is per session**: each session stores a `repo_path`; the agent runs with that cwd until the session is closed.
- **At most 5 sessions** total; close one before creating another.
- **Chat title**: defaults to the **workspace folder name** (last segment of `repo_path`). Pass `title` on **`POST /api/sessions`** to override.
- **One ACP process per open session**: follow-up messages use `session_prompt` on the same client — not a new subprocess per message.
- **Model**: set **only when creating** a session — exact `agent --model` id, or **Auto** (omit `--model`). It cannot be changed after creation. If unset at creation, dashboard **default model** (SQLite) / `acp.default_model` can still apply when spawning (see `session_manager._effective_model`).
- **Close** stops the agent and **removes** that session and its messages from the database (no separate “purge”).

## Telegram usage

1. Start the server with `TELEGRAM_BOT_TOKEN` set.
2. Open your bot, send `/start`.
3. **`/session_new`** — new chat at **workspace root** only (same as web **No repository**) · **`/repos`** — GitHub repos (`gh`) · **`/workspaces`** — local folders under the workspace root.
4. Plain messages continue the **open session** for that repo (same agent process until you close it).
5. **`/sessions`** — list and connect · **`/session_close`** — stop the agent for this chat · Choosing a repo or workspace sets the folder and starts a **new** session on your **next** message.

## Web usage

1. Open the dashboard; your browser gets a stable **client id** (localStorage) so sessions are scoped to you.
2. **Sidebar**: set **Default model** (saved in SQLite, `PUT /api/settings/default-model`) if you want new sessions to use a fixed `agent --model` id; then **New session** (repository **dropdown** via `GET /api/repo-picker`, per-session **model** overrides for that create only). **Sessions** list below.
3. **Select a session** to load history; the session header shows the model chosen at creation (read-only).
4. Type in the chat box to send.
5. **Close session** stops the agent and deletes that chat. **Close all** removes every session.
6. When the agent asks a question, answer from the dashboard for that session.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Troubleshooting: `agent` not found

**macOS / Linux / WSL:** Run `which agent` (or `command -v agent`) in the **same** environment you use for `python run.py`. Install the [Cursor CLI](https://cursor.com/docs/cli) and ensure its directory is on **`PATH`**, or set **`acp.command`** in `config.yaml` to the full path of the `agent` executable. The server also scans **`~/.local/bin`**, which matches a common install location.

**Windows (works in PowerShell but not from the IDE / service):** The install script usually puts the CLI in **`%USERPROFILE%\.local\bin\agent.exe`**. Terminals you open often add that to `PATH`, but **Python / Cursor / services may not**, so the control plane cannot see `agent`.

The server also scans **`~/.local/bin`**, **`%USERPROFILE%\.local\bin`**, and **`%LOCALAPPDATA%\cursor-agent`** when resolving the CLI, which often fixes GUI-launched Python without extra configuration.

If it still fails on Windows:

1. In PowerShell where `agent --version` works, run: **`(Get-Command agent).Source`**
2. Add **`%USERPROFILE%\.local\bin`** (or that folder) to the **Windows user or system PATH** and restart the app, **or** set an explicit executable in **`config.yaml`**, for example:  
   `acp.command: "C:\\Users\\You\\AppData\\Local\\cursor-agent\\agent.cmd"`  
   (Typical install folder has **`agent.cmd`** + **`agent.ps1`**: we prefer **`.cmd`** via `cmd.exe /c`, else **`.ps1`** via PowerShell, else **`.exe`** if present.)

## Notes

- ACP wire format may evolve with Cursor releases; adjust `control_plane/acp_client.py` if needed.
- **Process cleanup when closing a session:** on **Windows**, the server uses `terminate()` and best-effort `taskkill` for the agent process tree; on **macOS / Linux / WSL**, Unix termination (`terminate()` / process group) applies instead of `taskkill`.
