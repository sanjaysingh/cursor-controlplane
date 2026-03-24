# Cursor CLI Control Plane

Turn your personal machine into a remotely operated Cursor workstation: `cursor-controlplane` lets you start and control persistent **Cursor CLI** sessions from **Telegram** or a **web dashboard**, so you can review code, make changes, create commits, and open pull requests from anywhere while everything runs securely on your own computer.

## Prerequisites

- Python 3.11+
- [Cursor CLI](https://cursor.com/docs/cli) installed and available on `PATH`
- `CURSOR_API_KEY` set, or `agent login` completed on the same machine
- (Optional) Telegram bot token from [@BotFather](https://t.me/BotFather)
- (Optional) [GitHub CLI](https://cli.github.com/) (`gh`) for GitHub repo browsing — run `gh auth login` on the server host

## Installation

### Binary install (recommended)

Download and install the latest release binary with a single command:

**Linux / macOS**
```bash
curl -fsSL https://raw.githubusercontent.com/sanjaysingh/cursor-controlplane/main/scripts/install.sh | bash
```

**Windows (PowerShell)**
```powershell
irm https://raw.githubusercontent.com/sanjaysingh/cursor-controlplane/main/scripts/install.ps1 | iex
```

The binary is placed under `~/.local/bin` (Linux/macOS) or `%LOCALAPPDATA%\Programs\cursor-controlplane\` (Windows) and `PATH` is updated automatically.

### Install as a background service

To install the binary **and** register an auto-starting service in one step:

**Linux / macOS**
```bash
curl -fsSL https://raw.githubusercontent.com/sanjaysingh/cursor-controlplane/main/scripts/install.sh | bash -s -- --with-service
```

**Windows (PowerShell)**
```powershell
irm https://raw.githubusercontent.com/sanjaysingh/cursor-controlplane/main/scripts/install.ps1 | iex -WithService
```

Services created:
- **Linux:** systemd user unit `cursor-controlplane.service` — check with `systemctl --user status cursor-controlplane`. On headless servers run `loginctl enable-linger $USER` so the service starts without an interactive login. Logs: `journalctl --user -u cursor-controlplane.service -f` and a UTF-8 log file at `$XDG_DATA_HOME/cursor-controlplane/controlplane.log` (usually `~/.local/share/cursor-controlplane/controlplane.log`).
- **macOS:** LaunchAgent `com.cursor.controlplane` in `~/Library/LaunchAgents/`. Same log file path under `$XDG_DATA_HOME/cursor-controlplane/` (defaults to `~/.local/share/cursor-controlplane/controlplane.log`).
- **Windows:** Scheduled Task `CursorControlPlane` (runs at log on). Log file: `%LOCALAPPDATA%\cursor-controlplane\controlplane.log`.

The installers set `CONTROL_PLANE_LOG_FILE` to that path so the process appends logs to the file while still writing to the console (where the service manager captures them). For a foreground run, leave `CONTROL_PLANE_LOG_FILE` unset to use console logging only, or set it in `.env` to log to a file as well.

### Uninstall

**Linux / macOS**
```bash
curl -fsSL https://raw.githubusercontent.com/sanjaysingh/cursor-controlplane/main/scripts/uninstall.sh | bash
```

**Windows (PowerShell)**
```powershell
irm https://raw.githubusercontent.com/sanjaysingh/cursor-controlplane/main/scripts/uninstall.ps1 | iex
```

Removes the service (if present), the binary, and the data directory. Add `--keep-data` (Linux/macOS) or `-KeepData` (Windows) to preserve the SQLite database.

## Configuration

### 1. Environment variables

Copy the template and set values:

```bash
# Linux / macOS
cp .env.example .env

# Windows
copy .env.example .env
```

Set at minimum:

```
CURSOR_API_KEY=<your key>
TELEGRAM_BOT_TOKEN=<your token>   # only if using Telegram
```

### 2. config.yaml

Edit `config.yaml` in the repository root:

- **`repos`** — list of local repositories (name + path) to make available in the UI. Can also be managed via the web sidebar or Telegram commands.
- **`workspace_root`** — default folder for new workspaces (default: `~/cursor-control-plane`). Override with `CONTROL_PLANE_WORKSPACE_ROOT`.
- **`channels.telegram.enabled`** / **`channels.web.enabled`** — toggle each channel.
- **`acp.default_model`** — default `agent --model` value for new sessions (run `agent models` for available ids). Leave empty to let the agent choose.
- **`acp.stream_update_mode`** — `agent_message_chunk_only` (default, per ACP docs) or `all`.

### 3. Configure via CLI (no server start needed)

| Command | Purpose |
|---------|---------|
| `cursor-controlplane configure` | Full interactive setup wizard |
| `cursor-controlplane configure show` | Print resolved runtime config (paths, env, DB settings) |
| `cursor-controlplane configure telegram-token <TOKEN>` | Store Telegram bot token in DB |
| `cursor-controlplane configure telegram-allowlist <ids>` | Set allowed Telegram user IDs (comma- or space-separated) |

Settings stored in the DB take priority over environment variables and `config.yaml`. After changing configuration for a running service, apply with:

```bash
cursor-controlplane restart
```

## Running

### Binary

```bash
cursor-controlplane
```

### From source (development)

See [Development Setup](#development-setup) below.

## Usage

### Web dashboard

Open `http://localhost:8080/` (port configurable in `config.yaml`).

1. Click **New session**, pick a repository and model, then start chatting.
2. The **Default model** in the sidebar (saved in SQLite) applies to all new sessions unless overridden per session.
3. **Close session** stops the agent and deletes that chat. **Close all** removes every session.
4. When the agent asks a question, answer directly from the dashboard.

Sessions are scoped to your browser via a stable client id stored in `localStorage`.

### Telegram

1. Start the server with `TELEGRAM_BOT_TOKEN` set and open your bot.
2. Send `/start` to register.
3. Use these commands:

| Command | Action |
|---------|--------|
| `/session_new` | Start a new session at workspace root |
| `/workspaces` | Browse local folders under workspace root |
| `/repos` | Browse GitHub repos (requires `gh auth login`) |
| `/sessions` | List open sessions and switch between them |
| `/session_close` | Stop the agent for the current session |

Plain messages are forwarded to the active session's agent process.

## How sessions work

- **At most 5 sessions** open at once — close one before creating another.
- **One agent process per session**: follow-up messages reuse the same ACP client, not a new subprocess.
- **Workspace is fixed per session**: set at creation, cannot be changed. Closing a session stops the agent and removes all its messages from the database.
- **Model is fixed per session**: set at creation only. Use **Auto** to omit `--model` and let the agent decide.
- **Chat title** defaults to the workspace folder name; pass `title` on `POST /api/sessions` to override.

## API reference

| Endpoint | Description |
|----------|-------------|
| `GET /api/sessions` | List sessions |
| `POST /api/sessions` | Create session (`repo_path`, optional `model`, `title`) |
| `GET /api/sessions/{id}/messages` | Message history |
| `POST /api/sessions/{id}/message` | Send a message |
| `POST /api/sessions/{id}/close` | Close session (stops agent, deletes data) |
| `POST /api/sessions/{id}/answer` | Answer an agent question |
| `WebSocket /ws` | Real-time updates |
| `GET /api/models` | Available model ids (from `agent models`) |
| `GET /api/repo-picker` | Combined local + GitHub repo list for UI |
| `GET /api/workspaces` | Local workspace folders |
| `GET /api/github/repos` | GitHub repos via `gh` |
| `POST /api/github/clone` | Clone a GitHub repo |
| `GET /api/dashboard-config` | Dashboard bootstrap config |

## Development setup

### 1. Clone and create virtual environment

```bash
git clone https://github.com/sanjaysingh/cursor-controlplane.git
cd cursor-controlplane
python -m venv .venv
```

> On macOS/Linux, use `python3` if `python` is not available.

### 2. Activate and install dependencies

| Environment | Command |
|-------------|---------|
| macOS / Linux / WSL | `source .venv/bin/activate` |
| Windows — Command Prompt | `.venv\Scripts\activate.bat` |
| Windows — PowerShell | `.\.venv\Scripts\Activate.ps1` |

> If PowerShell blocks scripts, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

```bash
pip install -r requirements.txt
```

### 3. Configure and run

Follow the [Configuration](#configuration) steps above, then:

```bash
python run.py
```

> If you move the project to a new folder, delete `.venv` and recreate it — it embeds absolute paths.

### Running tests

**Python tests** (API, database, model parsing):

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

**Dashboard JS tests:**

```bash
npm install
npm test
```

Tests use isolated temporary state (`CONTROL_PLANE_DB_PATH` and `CONTROL_PLANE_CONFIG` set by fixtures) and do not affect your normal database.

CI runs both suites on every push and pull request via [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

### Building a local binary

```bash
pip install -r requirements-build.txt
pyinstaller --noconfirm cursor-controlplane.spec
# Output: dist/cursor-controlplane(.exe)
```

Release binaries for Linux, macOS, and Windows are built automatically when a `v*` tag is pushed via [`.github/workflows/release.yml`](.github/workflows/release.yml).

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Troubleshooting

### `agent` not found

**macOS / Linux / WSL:** Verify with `which agent`. Ensure the Cursor CLI directory is on `PATH`, or set `acp.command` in `config.yaml` to the full executable path. The server also scans `~/.local/bin` automatically.

**Windows:** The Cursor CLI is typically installed at `%USERPROFILE%\.local\bin\agent.exe`. If `agent` works in PowerShell but not when launched from the IDE or as a service:

1. In a working PowerShell session, run: `(Get-Command agent).Source`
2. Add that folder to the Windows user PATH, **or** set the path explicitly in `config.yaml`:
   ```yaml
   acp:
     command: "C:\\Users\\You\\AppData\\Local\\cursor-agent\\agent.cmd"
   ```

The server scans `%USERPROFILE%\.local\bin` and `%LOCALAPPDATA%\cursor-agent` automatically, which resolves most cases.

### Empty model dropdown in dashboard

1. Open browser DevTools → Console, filter `[cp-models]` — should show `GET /api/models` and `modelCount`.
2. Open `/api/models` in the Network tab — check the `models` array and any `error` field.
3. In the server terminal, check the `GET /models` log line; run `agent models` directly if the list is empty.

## Notes

- ACP wire format may evolve with Cursor releases; update `control_plane/acp_client.py` if needed.
- **Session process cleanup:** on Windows the server uses `terminate()` + best-effort `taskkill`; on macOS/Linux/WSL it uses Unix process group termination.
- **Data directory** for binary installs: `~/.local/share/cursor-controlplane/` (Linux/macOS) or `%APPDATA%\cursor-controlplane\` (Windows). Override with `CONTROL_PLANE_DATA_DIR` or `CONTROL_PLANE_DB_PATH`.
