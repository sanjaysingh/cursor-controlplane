"""Probe `agent acp` once to read model entries from session/new `configOptions` (authoritative for UI)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import re

from control_plane.acp_client import AcpClient
from control_plane.model_cli import cli_argv_model_for_agent

logger = logging.getLogger(__name__)


def _find_model_config_option(config_options: Any) -> dict[str, Any] | None:
    """Locate the model selector in a session/new `configOptions` array (ACP)."""
    if not isinstance(config_options, list):
        return None
    for o in config_options:
        if not isinstance(o, dict):
            continue
        if o.get("id") == "model" or o.get("category") == "model":
            return o
    for o in config_options:
        if not isinstance(o, dict):
            continue
        oid = str(o.get("id") or "").lower()
        cat = str(o.get("category") or "").lower()
        choices = o.get("options")
        if not isinstance(choices, list) or len(choices) < 2:
            continue
        if "model" in oid or "model" in cat:
            return o
    for o in config_options:
        if not isinstance(o, dict):
            continue
        choices = o.get("options")
        if o.get("type") == "select" and isinstance(choices, list) and len(choices) >= 10:
            return o
    return None


def _diagnose_session_new_result(result: Any) -> dict[str, Any]:
    """Structured summary for logs and API `diagnostics` when the model list is empty."""
    d: dict[str, Any] = {"result_type": type(result).__name__}
    if not isinstance(result, dict):
        return d
    d["result_keys"] = sorted(result.keys())
    co = result.get("configOptions")
    d["configOptions_type"] = type(co).__name__
    if isinstance(co, list):
        d["configOptions_len"] = len(co)
        # Summarize non-model options (id + category) for triage
        d["config_option_summaries"] = [
            {
                "id": (x.get("id") if isinstance(x, dict) else None),
                "category": (x.get("category") if isinstance(x, dict) else None),
                "type": (x.get("type") if isinstance(x, dict) else None),
                "options_count": len(x["options"])
                if isinstance(x, dict) and isinstance(x.get("options"), list)
                else None,
            }
            for x in co[:12]
            if isinstance(x, dict)
        ]
    else:
        d["configOptions_len"] = None
    opt = _find_model_config_option(co)
    d["model_config_option_found"] = opt is not None
    if isinstance(opt, dict):
        choices = opt.get("options")
        d["model_choices_count"] = len(choices) if isinstance(choices, list) else 0
        d["model_config_id"] = opt.get("id")
        d["model_config_category"] = opt.get("category")
    return d


def _dropdown_label_for_acp_model_value(acp_value: str) -> str:
    """
    Dropdown text should match CLI / stderr ids (e.g. composer-2-fast, gpt-5.3-codex), not ACP
    human titles (e.g. Composer 2).
    """
    t = acp_value.strip()
    base = t
    while base.endswith("[]"):
        base = base[:-2].rstrip()
    if base.lower() in ("default", "auto", "none"):
        return "auto"
    cli = cli_argv_model_for_agent(t)
    if cli:
        return cli
    if "[" in base:
        return base.split("[", 1)[0].strip()
    return base or t


def _options_to_models(model_option: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    choices = model_option.get("options")
    if not isinstance(choices, list):
        return out
    for c in choices:
        if not isinstance(c, dict):
            continue
        vid = c.get("value")
        if vid is None:
            continue
        vs = str(vid).strip()
        if not vs:
            continue
        # `id` = exact ACP value for DB + session/set_config_option; `name` = CLI-style label
        out.append({"id": vs, "name": _dropdown_label_for_acp_model_value(vs)})
    return out


async def probe_acp_model_options(
    workspace: str,
    *,
    agent_executable: str,
    extra_args: list[str],
    api_key: str | None,
    timeout: float = 120.0,
) -> tuple[list[dict[str, str]], str | None, dict[str, Any]]:
    """
    Start ACP, session/new, read model config option values (exact strings for DB + set_config_option).
    Returns (models, error_message, diagnostics).
    """
    ws = str(Path(workspace).resolve())
    diagnostics: dict[str, Any] = {
        "workspace_resolved": ws,
        "agent_executable": agent_executable,
        "extra_args": list(extra_args),
        "api_key_configured": bool(api_key),
    }
    client = AcpClient(
        workspace=ws,
        agent_executable=agent_executable,
        extra_args=list(extra_args),
        api_key=api_key,
        model=None,
    )
    try:
        async def _run() -> list[dict[str, str]]:
            logger.info("acp_model_probe: starting ACP subprocess workspace=%s agent=%s", ws, agent_executable)
            diagnostics["phase"] = "subprocess_start"
            await client.start()
            diagnostics["phase"] = "initialize"
            await client.initialize()
            diagnostics["phase"] = "authenticate"
            await client.authenticate()
            diagnostics["phase"] = "session_new"
            result = await client.request("session/new", {"cwd": ws, "mcpServers": []})
            sn = _diagnose_session_new_result(result)
            diagnostics["session_new"] = sn
            logger.info(
                "acp_model_probe: session/new done keys=%s model_option_found=%s choices=%s",
                sn.get("result_keys"),
                sn.get("model_config_option_found"),
                sn.get("model_choices_count"),
            )
            if not isinstance(result, dict):
                logger.warning("acp_model_probe: session/new result is not a dict: %r", result)
                return []
            opt = _find_model_config_option(result.get("configOptions"))
            if not opt:
                logger.warning(
                    "acp_model_probe: no model entry in configOptions (see diagnostics.config_option_summaries)"
                )
                return []
            parsed = _options_to_models(opt)
            logger.info("acp_model_probe: parsed %d models for dropdown", len(parsed))
            return parsed

        models = await asyncio.wait_for(_run(), timeout=timeout)
        diagnostics["phase"] = "complete"
        diagnostics["models_returned"] = len(models)
        diagnostics["ok"] = True
        return models, None, diagnostics
    except asyncio.TimeoutError:
        msg = f"Timed out after {timeout:.0f}s probing ACP for models."
        diagnostics["ok"] = False
        diagnostics["phase"] = "timeout"
        diagnostics["error"] = msg
        logger.error("acp_model_probe: %s workspace=%s", msg, ws)
        return [], msg, diagnostics
    except Exception as e:
        diagnostics["ok"] = False
        diagnostics["phase"] = "exception"
        diagnostics["error_type"] = type(e).__name__
        diagnostics["error_message"] = str(e)
        logger.exception("acp_model_probe: failed workspace=%s", ws)
        return [], str(e), diagnostics
    finally:
        try:
            await client.kill(grace_seconds=2.0)
        except Exception:
            pass
