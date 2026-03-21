"""Query Cursor CLI for available models (`agent --list-models` / `agent models`)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from control_plane.acp_client import _wrap_argv_for_windows_shims
from control_plane.agent_resolve import resolve_agent_executable
from control_plane.model_cli import (
    cli_model_id_for_argv,
    is_placeholder_cli_model_id,
    split_model_display_line,
)

logger = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _try_parse_json_models(s: str) -> list[dict[str, str]] | None:
    """Parse JSON object/array that may embed models (including array of string ids)."""
    s = s.strip()
    if not s:
        return None
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        # Embedded JSON: find first { or [ and attempt balanced parse
        for opener, closer in (("{", "}"), ("[", "]")):
            start = s.find(opener)
            if start < 0:
                continue
            depth = 0
            for i in range(start, len(s)):
                if s[i] == opener:
                    depth += 1
                elif s[i] == closer:
                    depth -= 1
                    if depth == 0:
                        chunk = s[start : i + 1]
                        try:
                            data = json.loads(chunk)
                        except json.JSONDecodeError:
                            break
                        out = _models_from_parsed_json(data)
                        if out:
                            return out
                        break
        return None
    out = _models_from_parsed_json(data)
    return out if out else None


def _models_from_parsed_json(data: Any) -> list[dict[str, str]]:
    if isinstance(data, list):
        return _dedupe([e for x in data if (e := _normalize_entry(x))])
    if isinstance(data, dict):
        for key in ("models", "data", "items", "results"):
            arr = data.get(key)
            if isinstance(arr, list):
                out = [e for x in arr if (e := _normalize_entry(x))]
                if out:
                    return _dedupe(out)
        e = _normalize_entry(data)
        return _dedupe([e]) if e else []
    return []


def _normalize_entry(item: Any) -> dict[str, str] | None:
    if isinstance(item, str):
        t = item.strip()
        if not t or _is_cli_noise_line(t):
            return None
        sp = split_model_display_line(t)
        if sp:
            if is_placeholder_cli_model_id(sp[0]):
                return None
            return {"id": sp[0], "name": sp[1]}
        if not _looks_like_model_slug(t):
            return None
        if is_placeholder_cli_model_id(t):
            return None
        return {"id": t, "name": t}
    if isinstance(item, dict):
        mid = item.get("id") or item.get("model") or item.get("modelId") or item.get("value")
        if mid is None and item.get("name"):
            mid = item.get("name")
        if mid is None:
            return None
        mid_s = str(mid).strip()
        if not mid_s:
            return None
        sp = split_model_display_line(mid_s)
        if sp:
            mid_s = sp[0]
        name = item.get("name") or item.get("label") or item.get("displayName") or item.get("title") or mid_s
        return {"id": mid_s, "name": str(name).strip()}
    return None


def _dedupe(models: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for m in models:
        i = m.get("id", "")
        if i and i not in seen:
            seen.add(i)
            out.append(m)
    return out


# Word-boundary phrases (avoid matching ids like `not-loading-model`).
_NOISE_LINE_RES = [
    re.compile(p, re.I)
    for p in (
        r"\bloading\s+models?\b",
        r"\bfetching\s+models?\b",
        r"\bavailable\s+models?\b",
        r"\bmodels?\s+available\b",
        r"\bplease\s+wait\b",
        r"\bselect\s+a\s+model\b",
        r"\bchoose\s+a\s+model\b",
        r"\bhere\s+are\s+(the|your)\b",
        r"\bno\s+models?\s+found\b",
        r"\bcould\s+not\s+load\b",
        r"\bfailed\s+to\s+load\b",
        r"\berror\s+loading\b",
        r"^warning\s*:",
        r"^usage\s*:",
        r"\bglobal\s+options\b",
        r"\bthe\s+following\s+models?\b",
    )
]


def _is_cli_noise_line(t: str) -> bool:
    """Headers / status text from `agent models` that are not model rows."""
    tl = t.strip().lower()
    if not tl:
        return True
    if tl.startswith("#") or tl.startswith("//"):
        return True
    for rx in _NOISE_LINE_RES:
        if rx.search(t):
            return True
    if tl == "model list" or tl.startswith("list of models"):
        return True
    # Titles with no slug-like token
    if re.fullmatch(r"[*\s:—\-–•·]*models?[*\s:—\-–•·]*", tl):
        return True
    if tl in {"models", "model", "loading...", "loading", "…"}:
        return True
    # Table header rows: "Name | Model" style with no model id characters
    if "|" in t and split_model_display_line(t) is None:
        cells = [c.strip() for c in t.split("|") if c.strip()]
        if cells and all(re.match(r"^[A-Za-z][A-Za-z\s]*$", c) for c in cells):
            return True
    return False


def _looks_like_model_slug(t: str) -> bool:
    """Heuristic: bare line is probably a model id, not UI copy."""
    t = t.strip()
    if not t or _is_cli_noise_line(t):
        return False
    if len(t) > 128:
        return False
    # Spaces usually mean a sentence (unless we already matched id - name elsewhere)
    if " " in t and split_model_display_line(t) is None:
        return False
    tl = t.lower()
    # Typical CLI ids: composer-1.5, claude-4-sonnet-thinking, gpt-4o, o3, etc.
    if re.match(r"^[a-z0-9][a-z0-9_.+/:-]*$", tl):
        return True
    return False


def _filter_noise_models(models: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop noise lines and pseudo-ids (auto/current/…) — UI adds a single **Auto** row separately."""
    return [
        m
        for m in models
        if m.get("id")
        and not _is_cli_noise_line(m["id"])
        and not is_placeholder_cli_model_id(m["id"])
    ]


def parse_models_output(stdout: str) -> list[dict[str, str]]:
    """Parse stdout from `agent --list-models` / `agent models` (JSON, JSONL, or plain lines)."""
    s = _strip_ansi(stdout).strip()
    if not s:
        return []

    jm = _try_parse_json_models(s)
    if jm:
        return _filter_noise_models(jm)

    lines = [ln.rstrip() for ln in s.splitlines() if ln.strip()]
    out: list[dict[str, str]] = []

    for ln in lines:
        t = ln.strip()
        if not t or t.startswith("#"):
            continue
        if _is_cli_noise_line(t):
            continue
        if re.match(r"^[-|:\s]+$", t):
            continue
        # JSON line
        if t.startswith("{") and t.endswith("}"):
            try:
                d = json.loads(t)
                if e := _normalize_entry(d):
                    out.append(e)
            except json.JSONDecodeError:
                pass
            continue
        # "Display name (model-id)"
        m = re.match(r"^(.+?)\s+\(([^)]+)\)\s*$", t)
        if m:
            name, mid = m.group(1).strip(), m.group(2).strip()
            mid_use = cli_model_id_for_argv(mid)
            if mid_use is None and is_placeholder_cli_model_id(mid):
                continue
            out.append({"id": mid_use or mid, "name": name})
            continue
        # "model-id - Human label" (common `agent models` text output)
        sp = split_model_display_line(t)
        if sp:
            if not is_placeholder_cli_model_id(sp[0]):
                out.append({"id": sp[0], "name": sp[1]})
            continue
        # Bullet / numbered list
        t = re.sub(r"^[\d]+[.)]\s+", "", t)
        t = re.sub(r"^[-*•]\s+", "", t).strip()
        if t:
            if _is_cli_noise_line(t):
                continue
            sp2 = split_model_display_line(t)
            if sp2:
                if not is_placeholder_cli_model_id(sp2[0]):
                    out.append({"id": sp2[0], "name": sp2[1]})
            elif _looks_like_model_slug(t) and not is_placeholder_cli_model_id(t):
                out.append({"id": t, "name": t})

    return _filter_noise_models(_dedupe(out))


async def _run_agent(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    argv = _wrap_argv_for_windows_shims(argv)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return -1, "", "Timed out waiting for agent to list models."
    rc = proc.returncode if proc.returncode is not None else -1
    out = out_b.decode("utf-8", errors="replace") if out_b else ""
    err = err_b.decode("utf-8", errors="replace") if err_b else ""
    return rc, out, err


async def list_cursor_models(agent_command: str, api_key: str | None) -> tuple[list[dict[str, str]], str | None]:
    """
    Run Cursor CLI to list models. Returns (models, error_message).
    error_message is set when no models could be parsed (CLI missing, auth, or unknown output).
    """
    try:
        exe = resolve_agent_executable(agent_command)
    except FileNotFoundError as e:
        logger.warning("list_cursor_models: %s", e)
        return [], str(e)

    env = os.environ.copy()
    if api_key:
        env["CURSOR_API_KEY"] = api_key

    # Order: prefer explicit JSON when CLI supports it; then plain list flags.
    attempts: list[list[str]] = []
    if api_key:
        attempts.append([exe, "--api-key", api_key, "--output-format", "json", "--list-models"])
        attempts.append([exe, "--api-key", api_key, "--list-models"])
        attempts.append([exe, "--api-key", api_key, "models", "--json"])
        attempts.append([exe, "--api-key", api_key, "models"])
    attempts.append([exe, "--output-format", "json", "--list-models"])
    attempts.append([exe, "--list-models"])
    attempts.append([exe, "models", "--json"])
    attempts.append([exe, "models"])

    last_stderr = ""
    last_out_sample = ""
    for argv in attempts:
        try:
            rc, out, err = await _run_agent(argv, env=env)
        except OSError as e:
            logger.exception("list_cursor_models spawn")
            return [], str(e)

        last_stderr = err.strip() or last_stderr
        blob = out
        if err.strip():
            blob = (out + "\n" + err) if out.strip() else err

        if rc != 0:
            logger.debug("list_cursor_models argv=%s rc=%s err=%s", argv, rc, err[:500] if err else "")
            # Still try to parse stdout/stderr (some builds exit non-zero with JSON body)
            models = parse_models_output(blob)
            if not models and out.strip():
                models = parse_models_output(out)
            if not models and err.strip():
                models = parse_models_output(err)
            if models:
                models.sort(key=lambda x: (x.get("name") or x.get("id") or "").lower())
                return models, None
            continue

        models = parse_models_output(blob)
        if not models and out.strip():
            models = parse_models_output(out)
        if not models and err.strip():
            models = parse_models_output(err)
        if models:
            models.sort(key=lambda x: (x.get("name") or x.get("id") or "").lower())
            return models, None

        if blob.strip():
            last_out_sample = _strip_ansi(blob)[:500]

    if last_out_sample:
        logger.info("list_cursor_models: could not parse CLI output sample: %s", last_out_sample)

    hint = (
        last_stderr[:800]
        if last_stderr
        else "No models returned. Is `agent` logged in (`agent login`) or `CURSOR_API_KEY` set?"
    )
    return [], hint
