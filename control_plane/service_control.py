"""Restart background service installed by scripts/install.sh or scripts/install.ps1."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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


def _macos_launchagent_plist() -> Path:
    return (Path.home() / "Library" / "LaunchAgents" / "com.cursor.controlplane.plist").resolve()


def _macos_binary_path() -> Path:
    return (Path.home() / ".local" / "bin" / "cursor-controlplane").resolve()


def _list_macos_service_pids(bin_path: Path) -> list[int]:
    try:
        r = subprocess.run(
            ["ps", "-axo", "pid=,uid=,command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if r.returncode != 0:
        return []
    out: list[int] = []
    uid = str(os.getuid())
    bin_str = str(bin_path)
    for raw in r.stdout.splitlines():
        parts = raw.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid_s, uid_s, exe, arg1 = parts[:4]
        if uid_s != uid or exe != bin_str or arg1 != "serve":
            continue
        try:
            out.append(int(pid_s))
        except ValueError:
            continue
    return out


def _wait_for_macos_process_exit(bin_path: Path, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _list_macos_service_pids(bin_path):
            return True
        time.sleep(0.5)
    return not _list_macos_service_pids(bin_path)


def _kill_macos_stale_processes(bin_path: Path) -> None:
    pids = _list_macos_service_pids(bin_path)
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    if _wait_for_macos_process_exit(bin_path):
        return
    for pid in _list_macos_service_pids(bin_path):
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    _wait_for_macos_process_exit(bin_path, timeout_s=5.0)


def _restart_macos_launchd(label: str) -> int:
    plist = _macos_launchagent_plist()
    bin_path = _macos_binary_path()
    if not plist.is_file():
        print(f"restart: launchd plist not found: {plist}", file=sys.stderr)
        return 1
    try:
        uid = os.getuid()
    except AttributeError:
        print("restart: launchd is only supported on macOS.", file=sys.stderr)
        return 1
    domain = f"gui/{uid}"
    subprocess.run(["launchctl", "bootout", domain, str(plist)], check=False)
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=False)
    subprocess.run(["launchctl", "unload", str(plist)], check=False)
    _kill_macos_stale_processes(bin_path)
    r = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=False)
    if r.returncode == 0:
        return 0
    r = subprocess.run(["launchctl", "load", str(plist)], check=False)
    return int(r.returncode)


def restart_service() -> int:
    """Restart using metadata in service.json. Returns process exit code (0 = success)."""
    marker = _read_marker()
    if not marker:
        print(
            "No service install metadata found.\n"
            f"Expected: {service_marker_path()}\n"
            "Install with: bash install.sh --with-service  "
            "or  install.ps1 -WithService",
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
        return _restart_macos_launchd(label)

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
