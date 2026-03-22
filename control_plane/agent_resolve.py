"""
Resolve the Cursor `agent` executable.

Windows GUI apps / IDE-launched Python often inherit a minimal PATH that does not include
`%USERPROFILE%\\.local\\bin`, where the Cursor install script places `agent.exe`.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _home_local_bin() -> Path:
    return Path.home() / ".local" / "bin"


def _extra_search_dirs() -> list[Path]:
    dirs: list[Path] = []
    hlb = _home_local_bin()
    dirs.append(hlb)
    # Some installs document user-level bin on Windows the same way as Unix
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        dirs.append(Path(userprofile) / ".local" / "bin")
    # Native Windows install (Get-Command agent often points at agent.ps1 here)
    if sys.platform == "win32":
        la = os.environ.get("LOCALAPPDATA")
        if la:
            ca = Path(la) / "cursor-agent"
            if ca.is_dir():
                dirs.append(ca)
    return dirs


def upgrade_ps1_path_to_better_shim(resolved: str) -> str:
    """
    PATH / Get-Command often points at agent.ps1. Prefer a real binary or agent.cmd
    in the same folder (typical layout: %LOCALAPPDATA%\\cursor-agent\\).
    """
    p = Path(resolved)
    if p.suffix.lower() != ".ps1":
        return resolved
    parent = p.parent
    for name in ("agent.exe", "cursor-agent.exe", "agent.cmd", "agent.bat"):
        cand = parent / name
        if cand.is_file():
            return str(cand.resolve())
    return resolved


def _try_file(path: Path) -> str | None:
    if path.is_file():
        return str(path.resolve())
    return None


def _scan_dir_for_agent(d: Path) -> str | None:
    if not d.is_dir():
        return None
    if sys.platform == "win32":
        for name in ("agent.exe", "agent.cmd", "agent.bat", "agent.ps1", "agent"):
            hit = _try_file(d / name)
            if hit:
                return hit
    else:
        hit = _try_file(d / "agent")
        if hit:
            return hit
    return None


def resolve_agent_executable(preferred: str) -> str:
    """
    Return a path or name suitable for subprocess argv[0].

    `preferred` is usually config ``acp.command`` (default ``agent``).
    """
    preferred = (preferred or "").strip()
    tried: list[str] = []

    # 1) Explicit path from user / config
    if preferred:
        tried.append(preferred)
        p = Path(preferred)
        if p.is_file():
            return upgrade_ps1_path_to_better_shim(str(p.resolve()))
        w = shutil.which(preferred)
        if w:
            return upgrade_ps1_path_to_better_shim(w)
        if sys.platform == "win32" and not preferred.lower().endswith(".exe"):
            w = shutil.which(f"{preferred}.exe")
            if w:
                return w

    # 2) Standard install dirs (~/.local/bin, %LOCALAPPDATA%/cursor-agent, …)
    for d in _extra_search_dirs():
        hit = _scan_dir_for_agent(d)
        if hit:
            return hit

    # 3) PATH lookup for common names
    for name in ("agent", "agent.exe") if sys.platform == "win32" else ("agent",):
        tried.append(name)
        w = shutil.which(name)
        if w:
            return upgrade_ps1_path_to_better_shim(w)

    hint = (
        "On Windows, open PowerShell where `agent --version` works and run:\n"
        "  (Get-Command agent).Source\n"
        "Set `acp.command` in config.yaml to agent.cmd or agent.ps1 in that folder (both are supported),\n"
        "or add that folder to your **user** or **system** PATH so Python can find the CLI."
    )
    if sys.platform == "win32":
        guess = _home_local_bin() / "agent.exe"
        hint = f"Typical install: {guess}\n\n{hint}"

    raise FileNotFoundError(
        "Cursor CLI (`agent`) not found. Tried: "
        + ", ".join(repr(t) for t in tried if t)
        + ".\n\n"
        + hint
    )
