"""GitHub CLI (`gh`) helpers: list repos, clone into workspace root."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from control_plane.workspace_paths import is_path_under_root

logger = logging.getLogger(__name__)

_clone_locks: dict[str, asyncio.Lock] = {}


def _clone_lock(key: str) -> asyncio.Lock:
    if key not in _clone_locks:
        _clone_locks[key] = asyncio.Lock()
    return _clone_locks[key]


def local_folder_name(name_with_owner: str) -> str:
    """Folder under workspace root: repo name only (e.g. owner/my-app -> my-app)."""
    s = name_with_owner.strip()
    if not s:
        return "repo"
    if "/" in s:
        return s.rsplit("/", 1)[-1].strip() or "repo"
    return s


async def gh_repo_list(*, limit: int = 40) -> tuple[list[dict[str, Any]], str | None]:
    """
    List repos for the authenticated `gh` user.
    Returns (rows with nameWithOwner, url, ...), error message or None.
    """
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    proc = await asyncio.create_subprocess_exec(
        "gh",
        "repo",
        "list",
        "--limit",
        str(limit),
        "--json",
        "nameWithOwner,url",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    err = err_b.decode(errors="replace").strip() if err_b else ""
    if proc.returncode != 0:
        msg = err or f"gh exited with {proc.returncode}"
        logger.warning("gh repo list failed: %s", msg)
        return [], msg
    try:
        data = json.loads(out_b.decode() or "[]")
    except json.JSONDecodeError as e:
        return [], f"Invalid JSON from gh: {e}"
    if not isinstance(data, list):
        return [], "Unexpected gh output shape"
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        nwo = item.get("nameWithOwner")
        if isinstance(nwo, str) and nwo.strip():
            rows.append(
                {
                    "nameWithOwner": nwo.strip(),
                    "url": item.get("url") if isinstance(item.get("url"), str) else "",
                }
            )
    return rows, None


async def gh_repo_clone(workspace_root: Path, name_with_owner: str) -> tuple[Path | None, str | None]:
    """
    Clone into workspace_root/<repo-name> if missing; else return existing path.
    Returns (resolved_path, error).
    """
    nwo = name_with_owner.strip()
    if not nwo or "/" not in nwo:
        return None, "Expected nameWithOwner like owner/repo"

    root = workspace_root.resolve()
    dest_name = local_folder_name(nwo)
    dest = (root / dest_name).resolve()

    if not is_path_under_root(root, dest):
        return None, "Invalid clone destination"

    async with _clone_lock(str(dest)):
        if dest.exists():
            if not dest.is_dir():
                return None, "Destination exists and is not a directory"
            if (dest / ".git").exists():
                return dest.resolve(), None
            try:
                if any(dest.iterdir()):
                    return dest.resolve(), None
            except OSError:
                return dest.resolve(), None
            try:
                dest.rmdir()
            except OSError:
                pass

        root.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "repo",
            "clone",
            nwo,
            str(dest),
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out_b, err_b = await proc.communicate()
        err = err_b.decode(errors="replace").strip() if err_b else ""
        if proc.returncode != 0:
            msg = err or f"gh clone failed ({proc.returncode})"
            logger.warning("gh repo clone failed: %s", msg)
            return None, msg
        if not dest.is_dir():
            return None, "Clone finished but folder missing"
        return dest.resolve(), None
