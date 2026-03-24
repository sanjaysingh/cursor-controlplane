#!/usr/bin/env bash
# Uninstall cursor-controlplane: stop user service (if any), remove binary, data directory.
#
# Usage:
#   bash uninstall.sh              # prompts [y/N]
#   bash uninstall.sh -y           # non-interactive (or CONTROL_PLANE_UNINSTALL_YES=1)
#   bash uninstall.sh --keep-data  # keep SQLite and service.json; remove only binary + service
#
# curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/uninstall.sh | bash -s -- -y

set -euo pipefail

YES=false
KEEP_DATA=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes)
      YES=true
      shift
      ;;
    --keep-data)
      KEEP_DATA=true
      shift
      ;;
    -h|--help)
      echo "Usage: uninstall.sh [-y|--yes] [--keep-data]" >&2
      echo "Env: CONTROL_PLANE_UNINSTALL_YES=1, CONTROL_PLANE_DATA_DIR (optional override for data dir)" >&2
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "${CONTROL_PLANE_UNINSTALL_YES:-}" == "1" ]]; then
  YES=true
fi

BIN="${HOME}/.local/bin/cursor-controlplane"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/cursor-controlplane"
if [[ -n "${CONTROL_PLANE_DATA_DIR:-}" ]]; then
  DATA_DIR="${CONTROL_PLANE_DATA_DIR/#\~/$HOME}"
fi
SYSTEMD_UNIT="${HOME}/.config/systemd/user/cursor-controlplane.service"
PLIST="${HOME}/Library/LaunchAgents/com.cursor.controlplane.plist"

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

if [[ "$YES" != true ]]; then
  read -r -p "Uninstall cursor-controlplane (service, binary, data)? [y/N] " reply
  r=$(printf '%s' "${reply:-}" | tr '[:upper:]' '[:lower:]')
  if [[ "$r" != "y" && "$r" != "yes" ]]; then
    echo "Aborted." >&2
    exit 1
  fi
fi

uname_s="$(uname -s)"

# --- Linux: systemd user service ---
if [[ "$uname_s" == "Linux" ]] && command -v systemctl >/dev/null 2>&1; then
  if [[ -f "${SYSTEMD_UNIT}" ]] || systemctl --user --quiet is-active cursor-controlplane.service 2>/dev/null; then
    systemctl --user stop cursor-controlplane.service 2>/dev/null || true
    systemctl --user disable cursor-controlplane.service 2>/dev/null || true
  fi
  rm -f "${SYSTEMD_UNIT}"
  systemctl --user daemon-reload 2>/dev/null || true
fi

# --- macOS: LaunchAgent ---
if [[ "$uname_s" == "Darwin" ]]; then
  launchctl bootout "gui/$(id -u)" "${PLIST}" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/com.cursor.controlplane" 2>/dev/null || true
  launchctl unload "${PLIST}" 2>/dev/null || true
  kill_macos_stale_processes
  rm -f "${PLIST}"
fi

# --- Binary ---
if [[ -f "${BIN}" ]]; then
  rm -f "${BIN}"
  echo "Removed ${BIN}" >&2
else
  echo "Binary not found: ${BIN} (skipping)" >&2
fi

# --- Data (DB, service.json, etc.) ---
if [[ "$KEEP_DATA" == true ]]; then
  echo "Kept data directory: ${DATA_DIR}" >&2
else
  if [[ -d "${DATA_DIR}" ]]; then
    rm -rf "${DATA_DIR}"
    echo "Removed data directory: ${DATA_DIR}" >&2
  else
    echo "Data directory not found: ${DATA_DIR} (skipping)" >&2
  fi
fi

echo "Uninstall finished. If ~/.local/bin was added to PATH only for this app, remove it from your shell profile." >&2
