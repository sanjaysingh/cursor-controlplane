#!/usr/bin/env bash
# Install latest cursor-controlplane binary from GitHub Releases.
#
# Usage:
#   export CONTROL_PLANE_REPO="your-org/cursor-controlplane"   # required once
#   curl -fsSL https://raw.githubusercontent.com/your-org/cursor-controlplane/main/scripts/install.sh | bash
#   curl -fsSL ... | bash -s -- --with-service
#
# Options:
#   --with-service, --service   Register a user service (systemd on Linux, LaunchAgent on macOS)
#   CONTROL_PLANE_INSTALL_SERVICE=1   Same as --with-service
#
# Or download this script, set CONTROL_PLANE_REPO, and run: bash install.sh [--with-service]

set -euo pipefail

WITH_SERVICE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-service|--service)
      WITH_SERVICE=true
      shift
      ;;
    -h|--help)
      echo "Usage: install.sh [--with-service|--service]" >&2
      echo "Env: CONTROL_PLANE_REPO (required), CONTROL_PLANE_INSTALL_SERVICE=1" >&2
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

REPO="${CONTROL_PLANE_REPO:-YOUR_ORG/cursor-controlplane}"
if [[ "$REPO" == YOUR_ORG/* ]]; then
  echo "Set CONTROL_PLANE_REPO to your GitHub org/repo (e.g. export CONTROL_PLANE_REPO=myorg/cursor-controlplane)" >&2
  exit 1
fi

uname_s="$(uname -s)"
uname_m="$(uname -m)"
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

URL="https://github.com/${REPO}/releases/latest/download/${ASSET}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

echo "Downloading ${URL} ..." >&2
curl -fsSL -o "$TMP" "$URL"
chmod +x "$TMP"

BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"
install -m 755 "$TMP" "${BIN_DIR}/cursor-controlplane"
BIN="${BIN_DIR}/cursor-controlplane"

echo "Installed ${BIN}" >&2
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  echo "Add to PATH, e.g. for bash/zsh:" >&2
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
fi

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/cursor-controlplane"
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
ExecStart=${BIN} serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now cursor-controlplane.service
  printf '%s\n' '{"type":"systemd-user","unit":"cursor-controlplane.service"}' >"${DATA_DIR}/service.json"
  echo "Installed systemd user service: cursor-controlplane.service (log: journalctl --user -u cursor-controlplane.service -f)" >&2
}

install_macos_service() {
  local plist="${HOME}/Library/LaunchAgents/com.cursor.controlplane.plist"
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
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/com.cursor.controlplane" 2>/dev/null || true
  if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
    :
  else
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
  fi
  printf '%s\n' '{"type":"launchd","label":"com.cursor.controlplane"}' >"${DATA_DIR}/service.json"
  echo "Installed LaunchAgent: $plist (restart: cursor-controlplane restart)" >&2
}

if [[ "$WITH_SERVICE" == true ]]; then
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
