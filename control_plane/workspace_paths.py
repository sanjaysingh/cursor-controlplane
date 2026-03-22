"""Workspace root resolution and listing top-level workspace folders."""

from __future__ import annotations

import logging
from pathlib import Path

from control_plane.config import AppConfig, EnvSettings

logger = logging.getLogger(__name__)


def resolve_workspace_root(app_config: AppConfig, env: EnvSettings) -> Path:
    raw = (env.workspace_root or "").strip() or (app_config.workspace_root or "").strip()
    if raw:
        p = Path(raw).expanduser()
    else:
        p = Path.home() / "cursor-control-plane"
    try:
        return p.resolve()
    except OSError as e:
        logger.warning("workspace root resolve failed, using home default: %s", e)
        return (Path.home() / "cursor-control-plane").resolve()


def is_path_under_root(root: Path, candidate: Path) -> bool:
    try:
        root_r = root.resolve()
        cand_r = candidate.resolve()
    except OSError:
        return False
    try:
        cand_r.relative_to(root_r)
        return True
    except ValueError:
        return False


def list_top_level_workspaces(root: Path) -> list[dict[str, str]]:
    """Immediate child directories of root (non-recursive)."""
    if not root.is_dir():
        return []
    out: list[dict[str, str]] = []
    try:
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            out.append({"name": child.name, "path": str(child.resolve())})
    except OSError as e:
        logger.warning("list_top_level_workspaces failed: %s", e)
    return out
