"""REST and WebSocket routes for the web dashboard."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from control_plane.models import (
    AnswerQuestionRequest,
    CloneGithubRepoRequest,
    CreateRunRequest,
    CreateSessionRequest,
    SendSessionMessageRequest,
)
from control_plane.acp_model_probe import probe_acp_model_options
from control_plane.agent_models import list_cursor_models
from control_plane.github_cli import gh_repo_clone, gh_repo_list
from control_plane.repo_picker import build_repo_picker_items
from control_plane.state import AppState
from control_plane.workspace_paths import list_top_level_workspaces, resolve_workspace_root

logger = logging.getLogger(__name__)

router = APIRouter()


def _web_channel_key(st: AppState) -> str:
    """Single web dashboard identity until auth; configurable via WEB_CHANNEL_KEY."""
    k = (st.env.web_channel_key or "").strip()
    return k if k else "web:default"


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/dashboard-config")
async def dashboard_config(request: Request) -> JSONResponse:
    """Web UI: fixed web identity (matches WEB_CHANNEL_KEY / default participant for streams)."""
    st: AppState = request.app.state.control_plane
    root = resolve_workspace_root(st.config, st.env)
    return JSONResponse(
        content={
            "web_channel_key": _web_channel_key(st),
            "workspace_root": str(root),
        }
    )


@router.get("/workspaces")
async def list_workspaces(request: Request) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    root = resolve_workspace_root(st.config, st.env)
    return JSONResponse(content=list_top_level_workspaces(root))


@router.get("/github/repos")
async def github_repos(request: Request, limit: int = 40) -> JSONResponse:
    rows, err = await gh_repo_list(limit=limit)
    return JSONResponse(content={"repos": rows, "error": err or ""})


@router.post("/github/clone")
async def github_clone_route(request: Request, body: CloneGithubRepoRequest) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    root = resolve_workspace_root(st.config, st.env)
    path, err = await gh_repo_clone(root, body.name_with_owner.strip())
    if err:
        return JSONResponse(status_code=400, content={"error": err})
    return JSONResponse(content={"path": str(path)})


@router.get("/repo-picker")
async def repo_picker(request: Request, gh_limit: int = 80) -> JSONResponse:
    """Deduped local + GitHub entries for the New session repository dropdown."""
    st: AppState = request.app.state.control_plane
    if gh_limit < 1:
        gh_limit = 1
    if gh_limit > 100:
        gh_limit = 100
    items, gh_err = await build_repo_picker_items(st.config, st.env, gh_limit=gh_limit)
    return JSONResponse(content={"items": items, "error": gh_err or ""})


@router.get("/repos")
async def list_repos(request: Request) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    return JSONResponse(
        content=[{"name": r.name, "path": r.path, "description": r.description} for r in st.config.repos]
    )


@router.get("/models")
async def list_models(request: Request) -> JSONResponse:
    """Models from `agent models` / `--list-models` — exact strings valid for `agent --model <id>`."""
    st: AppState = request.app.state.control_plane
    agent_bin = (st.env.cursor_agent_bin or st.config.acp.command or "agent").strip()
    api_key = (st.env.cursor_api_key or "").strip() or None
    models, err = await list_cursor_models(agent_bin, api_key)
    # One field only: dropdown label === value === CLI id (no separate display name).
    rows = [{"id": m["id"], "name": m["id"]} for m in models if m.get("id")]
    logger.info("GET /models cli model count=%d error=%r", len(rows), err)
    return JSONResponse(content={"models": rows, "error": err, "source": "cli"})


@router.get("/models/acp")
async def list_models_acp(request: Request, workspace: str = "") -> JSONResponse:
    """
    Models exactly as advertised by ACP `session/new` configOptions for the given workspace.
    Use this for the dashboard dropdown so every entry maps to a valid session/set_config_option value.
    """
    st: AppState = request.app.state.control_plane
    raw = (workspace or "").strip()
    if not raw:
        logger.info("GET /models/acp skipped: empty workspace query param")
        return JSONResponse(
            content={
                "models": [],
                "error": "Pass a workspace query parameter (absolute folder path for the repo).",
                "source": "acp",
                "diagnostics": {"reason": "missing_workspace_param"},
            }
        )
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError as e:
        logger.warning("GET /models/acp path resolve failed: %s raw=%r", e, raw[:200])
        return JSONResponse(
            content={
                "models": [],
                "error": str(e),
                "source": "acp",
                "diagnostics": {"reason": "path_resolve_error", "raw": raw[:500]},
            }
        )
    if not p.is_dir():
        logger.warning("GET /models/acp not a directory: %s", p)
        return JSONResponse(
            content={
                "models": [],
                "error": f"Not a directory: {p}",
                "source": "acp",
                "diagnostics": {"reason": "not_a_directory", "path": str(p)},
            }
        )

    agent_bin = (st.env.cursor_agent_bin or st.config.acp.command or "agent").strip()
    api_key = (st.env.cursor_api_key or "").strip() or None
    extra = list(st.config.acp.extra_args)
    models, err, diagnostics = await probe_acp_model_options(
        str(p),
        agent_executable=agent_bin,
        extra_args=extra,
        api_key=api_key,
    )
    n = len(models)
    logger.info(
        "GET /models/acp path=%s models=%d error=%r agent_bin=%s api_key_set=%s",
        p,
        n,
        err,
        agent_bin,
        bool(api_key),
    )
    if n == 0:
        logger.warning(
            "GET /models/acp returned 0 models; diagnostics=%s",
            diagnostics,
        )
    return JSONResponse(
        content={
            "models": models,
            "error": err,
            "source": "acp",
            "diagnostics": diagnostics,
        }
    )


# --- Conversational sessions ---


@router.get("/sessions")
async def list_sessions(
    request: Request,
    include_closed: bool = True,
) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    sessions = await st.session_manager.list_all_sessions_global(
        include_closed=include_closed, limit=100
    )
    return JSONResponse(content=[s.to_public_dict() for s in sessions])


@router.post("/sessions")
async def create_session(request: Request, body: CreateSessionRequest) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    p = Path(body.repo_path)
    if not p.is_dir():
        return JSONResponse(status_code=400, content={"error": "Not a valid directory"})
    s = await st.session_manager.create_session(
        "web",
        _web_channel_key(st),
        str(p.resolve()),
        body.title,
        model=body.model,
    )
    return JSONResponse(content=s.to_public_dict())


@router.post("/sessions/close-all")
async def close_all_sessions(request: Request) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    count = await st.session_manager.close_all_open_sessions_globally()
    return JSONResponse(content={"ok": True, "closed": count})


@router.post("/sessions/purge")
async def purge_all_sessions(request: Request) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    count = await st.session_manager.purge_all_sessions()
    return JSONResponse(content={"ok": True, "deleted": count})


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    s = await st.session_manager.get_session_public(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return JSONResponse(content=s.to_public_dict())


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, request: Request) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    if not await st.session_manager.get_session_public(session_id):
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    msgs = await st.session_manager.list_session_messages(session_id)
    return JSONResponse(content=msgs)


@router.post("/sessions/{session_id}/join")
async def join_session(session_id: str, request: Request) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    s = await st.session_manager.join_session(session_id, "web", _web_channel_key(st))
    if not s:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return JSONResponse(content=s.to_public_dict())


@router.post("/sessions/{session_id}/message")
async def post_message(
    session_id: str,
    request: Request,
    body: SendSessionMessageRequest,
) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    if not body.text.strip():
        return JSONResponse(status_code=400, content={"error": "text required"})
    try:
        s = await st.session_manager.send_session_message(
            session_id,
            body.text,
            participant_channel="web",
            participant_conversation_id=_web_channel_key(st),
        )
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    except Exception as e:
        logger.exception("send_session_message")
        return JSONResponse(status_code=500, content={"error": str(e)})
    return JSONResponse(content=s.to_public_dict())


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: str, request: Request) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    ok = await st.session_manager.close_session(session_id)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return JSONResponse(content={"ok": True, "session_id": session_id})


@router.post("/sessions/{session_id}/answer")
async def answer_question(
    session_id: str,
    request: Request,
    payload: AnswerQuestionRequest,
) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    ok = await st.session_manager.answer_web_question(session_id, payload.answer, _web_channel_key(st))
    if not ok:
        return JSONResponse(status_code=404, content={"error": "No pending question for this session"})
    return JSONResponse(content={"ok": True})


# --- Legacy aliases (same UUID as session id) ---


@router.get("/runs")
async def list_runs_legacy(
    request: Request,
    include_completed: bool = True,
) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    sessions = await st.session_manager.list_all_sessions_global(
        include_closed=include_completed, limit=100
    )
    return JSONResponse(content=[s.to_public_dict() for s in sessions])


@router.post("/runs")
async def create_run_legacy(request: Request, body: CreateRunRequest) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    s = await st.session_manager.legacy_create_run(body.conversation_id, body.repo_path, body.prompt)
    if not s:
        return JSONResponse(status_code=400, content={"error": "Could not start (invalid repo?)"})
    return JSONResponse(content=s.to_public_dict())


@router.post("/runs/{session_id}/stop")
async def stop_run_legacy(session_id: str, request: Request) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    ok = await st.session_manager.close_session(session_id)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return JSONResponse(content={"ok": True, "session_id": session_id})


@router.post("/runs/{session_id}/answer")
async def answer_legacy(
    session_id: str,
    request: Request,
    payload: AnswerQuestionRequest,
) -> JSONResponse:
    st: AppState = request.app.state.control_plane
    ok = await st.session_manager.answer_web_question(session_id, payload.answer, _web_channel_key(st))
    if not ok:
        return JSONResponse(status_code=404, content={"error": "No pending question"})
    return JSONResponse(content={"ok": True})


async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    st: AppState = websocket.app.state.control_plane

    async def push(ev: dict[str, Any]) -> None:
        try:
            await websocket.send_text(json.dumps(ev))
        except Exception:
            pass

    st.hub.subscribe(push)
    try:
        await websocket.send_text(json.dumps({"type": "hello"}))
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    finally:
        st.hub.unsubscribe(push)
