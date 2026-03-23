"""Restart background service installed by scripts/install.sh or scripts/install.ps1."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from control_plane.paths import service_marker_path


def _read_marker() -> dict[str, Any] | None:
    path = service_marker_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def restart_service() -> int:
    """Restart using metadata in service.json. Returns process exit code (0 = success)."""
    marker = _read_marker()
    if not marker:
        print(
            "No service install metadata found.\n"
            f"Expected: {service_marker_path()}\n"
            "Install with: bash install.sh --with-service  "
            "or  install.ps1 -WithService  "
            "(and set CONTROL_PLANE_REPO).",
            file=sys.stderr,
        )
        return 1

    kind = marker.get("type")
    if kind == "systemd-user":
        unit = str(marker.get("unit") or "cursor-controlplane.service")
        cmd = ["systemctl", "--user", "restart", unit]
        r = subprocess.run(cmd, check=False)
        return int(r.returncode)

    if kind == "launchd":
        label = str(marker.get("label") or "com.cursor.controlplane")
        try:
            uid = os.getuid()
        except AttributeError:
            print("restart: launchd is only supported on macOS.", file=sys.stderr)
            return 1
        target = f"gui/{uid}/{label}"
        cmd = ["launchctl", "kickstart", "-k", target]
        r = subprocess.run(cmd, check=False)
        return int(r.returncode)

    if kind == "scheduled-task":
        if sys.platform != "win32":
            print("restart: scheduled-task is only for Windows.", file=sys.stderr)
            return 1
        name = str(marker.get("name") or "CursorControlPlane")
        ps = (
            f"$t = Get-ScheduledTask -TaskName '{name}' -ErrorAction Stop; "
            f"Stop-ScheduledTask -InputObject $t -ErrorAction SilentlyContinue; "
            f"Start-ScheduledTask -InputObject $t"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            check=False,
        )
        return int(r.returncode)

    print(f"restart: unknown service type in marker: {kind!r}", file=sys.stderr)
    return 1
