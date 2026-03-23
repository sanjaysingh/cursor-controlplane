"""Runtime paths: project root, data directory, SQLite file (supports PyInstaller frozen)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running as a PyInstaller (or similar) one-file/one-dir bundle."""
    return bool(getattr(sys, "frozen", False))


def _project_root() -> Path:
    """Directory containing the `control_plane` package (dev) or bundle root (frozen)."""
    if is_frozen():
        # onefile: sys._MEIPASS is extraction dir; onefolder: executable dir is common
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_shared_data_dir() -> Path:
    """Per-user data dir (matches install.sh / install.ps1 and release/frozen DB layout).

    Use this for service-install metadata so `cursor-controlplane restart` finds the same paths
    whether the binary was installed via the script or you override CONTROL_PLANE_DATA_DIR.
    """
    override = (os.environ.get("CONTROL_PLANE_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return (Path(base) / "cursor-controlplane").resolve()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return (Path(xdg) / "cursor-controlplane").resolve()
    return (Path.home() / ".local" / "share" / "cursor-controlplane").resolve()


def default_data_dir() -> Path:
    """User-writable directory for DB and local state."""
    if is_frozen():
        return user_shared_data_dir()
    return (_project_root() / "data").resolve()


def service_marker_path() -> Path:
    """Path to JSON written by install scripts when --with-service is used."""
    override = (os.environ.get("CONTROL_PLANE_SERVICE_MARKER") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return user_shared_data_dir() / "service.json"


def database_path() -> Path:
    """SQLite path: CONTROL_PLANE_DB_PATH, else data dir / control_plane.db."""
    override = (os.environ.get("CONTROL_PLANE_DB_PATH") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_data_dir() / "control_plane.db"


def static_package_dir() -> Path:
    """Directory with dashboard static files (bundled under PyInstaller _MEIPASS when frozen)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / "control_plane" / "static"
    return Path(__file__).resolve().parent / "static"
