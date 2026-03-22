"""Unified repo/workspace options for the web dashboard dropdown."""

from __future__ import annotations

import logging

from control_plane.config import AppConfig, EnvSettings
from control_plane.github_cli import gh_repo_list, local_folder_name
from control_plane.workspace_paths import list_top_level_workspaces, resolve_workspace_root

logger = logging.getLogger(__name__)


def _repo_name_only(name_with_owner: str) -> str:
    s = name_with_owner.strip()
    if "/" in s:
        return s.rsplit("/", 1)[-1]
    return s


async def build_repo_picker_items(
    app_config: AppConfig,
    env: EnvSettings,
    *,
    gh_limit: int = 80,
) -> tuple[list[dict[str, str | None]], str | None]:
    """
    Local folders (local-<name>) + GitHub repos (github-<repo>) not already present as clones.
    Dedup: if gh clone target folder already exists under workspace root, omit the GitHub row.
    """
    root = resolve_workspace_root(app_config, env)
    locals_ = list_top_level_workspaces(root)
    local_by_name = {e["name"]: e["path"] for e in locals_}

    gh_rows, gh_err = await gh_repo_list(limit=gh_limit)

    items: list[dict[str, str | None]] = []
    li = 0
    for e in locals_:
        name = e.get("name") or ""
        path = e.get("path") or ""
        if not name or not path:
            continue
        items.append(
            {
                "id": f"l{li}",
                "kind": "local",
                "label": f"local-{name}",
                "path": path,
                "nameWithOwner": None,
            }
        )
        li += 1

    label_counts: dict[str, int] = {}
    gi = 0
    for row in gh_rows:
        if not isinstance(row, dict):
            continue
        nwo = row.get("nameWithOwner")
        if not isinstance(nwo, str) or not nwo.strip():
            continue
        nwo = nwo.strip()
        dest_name = local_folder_name(nwo)
        if dest_name in local_by_name:
            continue
        dest_path = root / dest_name
        try:
            if dest_path.is_dir():
                continue
        except OSError:
            logger.debug("repo_picker: skip check %s", dest_path)

        repo_only = _repo_name_only(nwo)
        base_label = f"github-{repo_only}"
        cnt = label_counts.get(base_label, 0)
        label_counts[base_label] = cnt + 1
        if cnt == 0:
            label = base_label
        else:
            owner = nwo.split("/", 1)[0]
            label = f"github-{owner}-{repo_only}"

        items.append(
            {
                "id": f"g{gi}",
                "kind": "github",
                "label": label,
                "path": None,
                "nameWithOwner": nwo,
            }
        )
        gi += 1

    return items, gh_err
