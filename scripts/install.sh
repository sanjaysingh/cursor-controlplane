#!/usr/bin/env bash
# Install latest cursor-controlplane binary from GitHub Releases.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/sanjaysingh/cursor-controlplane/main/scripts/install.sh | bash
#   curl -fsSL ... | bash -s -- --with-service
#
# Options:
#   --with-service, --service   Register a user service (systemd on Linux, LaunchAgent on macOS)
#   CONTROL_PLANE_INSTALL_SERVICE=1   Same as --with-service
#   CONTROL_PLANE_REPO                Override the GitHub org/repo (default: sanjaysingh/cursor-controlplane)

set -euo pipefail

# Dependency checks
check_dependencies() {
  local missing=()
  if ! command -v git >/dev/null 2>&1; then
    missing+=("git")
  fi
  if ! command -v gh >/dev/null 2>&1; then
    missing+=("gh (GitHub CLI)")
  fi
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Error: Missing required dependencies: ${missing[*]}" >&2
    echo "Please install them before continuing:" >&2
    echo "  - git: https://git-scm.com/downloads" >&2
    echo "  - gh: https://cli.github.com/" >&2
    if [[ "$uname_s" == "Darwin" ]]; then
      echo "  Hint: brew install git gh" >&2
    elif [[ "$uname_s" == "Linux" ]]; then
      echo "  Hint: sudo apt install git  # and download gh from GitHub" >&2
    fi
    exit 1
  fi
}

WITH_SERVICE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-service|--service)
      WITH_SERVICE=true
      shift
      ;;
    -h|--help)
      echo "Usage: install.sh [--with-service|--service]" >&2
      echo "Env: CONTROL_PLANE_REPO (default: sanjaysingh/cursor-controlplane), CONTROL_PLANE_INSTALL_SERVICE=1" >&2
      exit 0
      ;;
    *)
      echo "Unknown option: $1 (use --help)" >&2
      exit 1
      ;;
  esac
done

if [[ "${CONTROL_PLANE_INSTALL_SERVICE:-}" == "1" ]]; then
  WITH_SERVICE=true
fi

REPO="${CONTROL_PLANE_REPO:-sanjaysingh/cursor-controlplane}"

uname_s="$(uname -s)"
uname_m="$(uname -m)"
BIN_DIR="${HOME}/.local/bin"
BIN="${BIN_DIR}/cursor-controlplane"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/cursor-controlplane"
SYSTEMD_UNIT="${HOME}/.config/systemd/user/cursor-controlplane.service"
PLIST="${HOME}/Library/LaunchAgents/com.cursor.controlplane.plist"
case "${uname_s}-${uname_m}" in
  Linux-x86_64|Linux-amd64)
    ASSET="cursor-controlplane-linux-amd64"
    ;;
  Darwin-arm64)
    ASSET="cursor-controlplane-macos-arm64"
    ;;
  Darwin-x86_64)
    echo "Intel macOS is not built by default in CI; use Python from source or extend .github/workflows/release.yml." >&2
    exit 1
    ;;
  *)
    echo "Unsupported platform: ${uname_s} ${uname_m}" >&2
    exit 1
    ;;
esac

check_dependencies

service_installed=false
case "$uname_s" in
  Linux)
    if [[ -f "${SYSTEMD_UNIT}" ]]; then
      service_installed=true
    fi
    ;;
  Darwin)
    if [[ -f "${PLIST}" ]]; then
      service_installed=true
    fi
    ;;
esac

should_install_service=false
if [[ "$WITH_SERVICE" == true || "$service_installed" == true ]]; then
  should_install_service=true
fi

stop_linux_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return
  fi
  echo "Stopping existing systemd user service..." >&2
  systemctl --user stop cursor-controlplane.service 2>/dev/null || true
}

list_macos_service_pids() {
  ps -axo pid=,uid=,command= | awk -v uid="$(id -u)" -v bin="$BIN" '
    $2 == uid && $3 == bin && $4 == "serve" { print $1 }
  '
}

wait_for_macos_process_exit() {
  local waited=0
  while [[ "$waited" -lt 10 ]]; do
    if [[ -z "$(list_macos_service_pids)" ]]; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

wait_for_macos_process_start() {
  local waited=0
  while [[ "$waited" -lt 10 ]]; do
    if [[ -n "$(list_macos_service_pids)" ]]; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

kill_macos_stale_processes() {
  local pids
  pids="$(list_macos_service_pids)"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  echo "Stopping stale cursor-controlplane processes..." >&2
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    kill "$pid" 2>/dev/null || true
  done <<< "$pids"
  if wait_for_macos_process_exit; then
    return 0
  fi
  echo "Force-killing stale cursor-controlplane processes..." >&2
  pids="$(list_macos_service_pids)"
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    kill -9 "$pid" 2>/dev/null || true
  done <<< "$pids"
  if wait_for_macos_process_exit; then
    return 0
  fi
  echo "Could not stop stale cursor-controlplane processes." >&2
  return 1
}

stop_macos_service() {
  echo "Stopping existing LaunchAgent..." >&2
  launchctl bootout "gui/$(id -u)" "${PLIST}" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/com.cursor.controlplane" 2>/dev/null || true
  kill_macos_stale_processes
}

if [[ "$service_installed" == true ]]; then
  case "$uname_s" in
    Linux) stop_linux_service ;;
    Darwin) stop_macos_service ;;
  esac
fi

URL="https://github.com/${REPO}/releases/latest/download/${ASSET}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

echo "Downloading ${URL} ..." >&2
curl -fsSL -o "$TMP" "$URL"
chmod +x "$TMP"

mkdir -p "$BIN_DIR"
install -m 755 "$TMP" "${BIN_DIR}/cursor-controlplane"

echo "Installed ${BIN}" >&2
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  echo "Add to PATH, e.g. for bash/zsh:" >&2
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
fi

mkdir -p "$DATA_DIR"

install_linux_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; cannot install systemd user service." >&2
    exit 1
  fi
  local unit_dir="${HOME}/.config/systemd/user"
  mkdir -p "$unit_dir"
  cat >"${unit_dir}/cursor-controlplane.service" <<EOF
[Unit]
Description=Cursor Control Plane
After=network-online.target

[Service]
Type=simple
Environment=CONTROL_PLANE_LOG_FILE=${DATA_DIR}/controlplane.log
ExecStart=${BIN} serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable cursor-controlplane.service >/dev/null
  systemctl --user reset-failed cursor-controlplane.service 2>/dev/null || true
  systemctl --user start cursor-controlplane.service
  printf '%s\n' '{"type":"systemd-user","unit":"cursor-controlplane.service"}' >"${DATA_DIR}/service.json"
  echo "Installed systemd user service: cursor-controlplane.service (logs: journalctl --user -u cursor-controlplane.service -f, and ${DATA_DIR}/controlplane.log)" >&2
}

install_macos_service() {
  local plist="${HOME}/Library/LaunchAgents/com.cursor.controlplane.plist"
  local launchd_path="${BIN_DIR}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  mkdir -p "${HOME}/Library/LaunchAgents"
  cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.cursor.controlplane</string>
  <key>ProgramArguments</key>
  <array>
    <string>${BIN}</string>
    <string>serve</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CONTROL_PLANE_LOG_FILE</key>
    <string>${DATA_DIR}/controlplane.log</string>
    <key>PATH</key>
    <string>${launchd_path}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)" "${plist}" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/com.cursor.controlplane" 2>/dev/null || true
  kill_macos_stale_processes
  if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
    :
  else
    launchctl kickstart -k "gui/$(id -u)/com.cursor.controlplane" 2>/dev/null || true
    launchctl load "$plist"
  fi
  if ! wait_for_macos_process_start; then
    echo "LaunchAgent did not start cursor-controlplane successfully." >&2
    exit 1
  fi
  printf '%s\n' '{"type":"launchd","label":"com.cursor.controlplane"}' >"${DATA_DIR}/service.json"
  echo "Installed LaunchAgent: $plist (log file: ${DATA_DIR}/controlplane.log; restart: cursor-controlplane restart)" >&2
}

if [[ "$should_install_service" == true ]]; then
  case "$uname_s" in
    Linux) install_linux_service ;;
    Darwin) install_macos_service ;;
    *)
      echo "--with-service is only supported on Linux (systemd) and macOS (launchd)." >&2
      exit 1
      ;;
  esac
  echo "Configure anytime with: cursor-controlplane configure" >&2
  echo "Apply changes: cursor-controlplane restart" >&2
fi
