"""Normalize model strings for `agent --model` (CLI id vs human-readable list lines)."""

from __future__ import annotations

# Cursor CLI lists pseudo-ids like `current` meaning "account default". They are not valid
# for `agent --model` with `acp` and can crash the subprocess if passed through.
_PLACEHOLDER_MODEL_IDS = frozenset({"current", "default", "auto", "none"})


def is_placeholder_cli_model_id(model_id: str | None) -> bool:
    if model_id is None:
        return False
    return str(model_id).strip().lower() in _PLACEHOLDER_MODEL_IDS


def split_model_display_line(line: str) -> tuple[str, str] | None:
    """
    Cursor CLI often prints models as `model-id - Human name`.
    `agent --model` only accepts the id (left side).
    """
    for sep in (" — ", " – ", " - "):
        if sep in line:
            left, _, right = line.partition(sep)
            left, right = left.strip(), right.strip()
            if left and right:
                return (left, right)
    return None


def cli_model_id_for_argv(raw: str | None) -> str | None:
    """Strip display suffix so the value is safe for `agent --model`."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    sp = split_model_display_line(s)
    out = sp[0] if sp else s
    if is_placeholder_cli_model_id(out):
        return None
    return out


def cli_argv_model_for_agent(raw: str | None) -> str | None:
    """
    Build the string to pass to `agent --model` when the stored value is an ACP
    `configOptions` id (e.g. ``composer-1.5[]``, ``composer-2[fast=true]``).
    Those forms are rejected by the CLI; this maps them to the short ids the agent prints
    in stderr (e.g. ``composer-1.5``, ``composer-2-fast``).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    sp = split_model_display_line(s)
    s = sp[0] if sp else s

    # composer-1.5[]  ->  composer-1.5
    while s.endswith("[]"):
        s = s[:-2].rstrip()
    if not s:
        return None

    if "[" not in s:
        return None if is_placeholder_cli_model_id(s) else s

    base, _, rest = s.partition("[")
    base = base.strip()
    inner = rest.rstrip("]").strip().lower()

    if not base or is_placeholder_cli_model_id(base):
        return None

    if "fast=true" in inner:
        if base.endswith("-fast"):
            return base
        return f"{base}-fast"

    # Remaining bracket metadata is ACP-specific; CLI usually accepts the base slug.
    return None if is_placeholder_cli_model_id(base) else base
