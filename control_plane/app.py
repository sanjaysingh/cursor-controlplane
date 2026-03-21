"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from control_plane.api.routes import router as api_router, websocket_endpoint
from control_plane.config import get_settings
from control_plane.db import Database
from control_plane.events import EventHub
from control_plane.channels.registry import ChannelRegistry
from control_plane.channels.web_channel import WebChannel
from control_plane.channels.telegram_channel import TelegramChannel
from control_plane.session_manager import SessionManager
from control_plane.state import AppState

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    st: AppState = app.state.control_plane
    await st.db.init_schema()
    for r in st.config.repos:
        await st.db.upsert_repo(r.name, r.path, r.description)

    await st.registry.get("web").start()
    tg = st.registry.all().get("telegram")
    if tg:
        await tg.start()
        logger.info("Telegram channel started")
    else:
        logger.info("Telegram channel disabled or missing token")

    yield

    for ch in st.registry.all().values():
        try:
            await ch.stop()
        except Exception:
            logger.exception("Channel stop failed")


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app_config, env = get_settings()
    hub = EventHub()
    registry = ChannelRegistry()
    db = Database(Path(__file__).resolve().parent.parent / "data" / "control_plane.db")
    session_manager = SessionManager(db, app_config, env, registry, hub)
    web = WebChannel(hub)
    registry.register(web)
    if app_config.channels.telegram.get("enabled") and env.telegram_bot_token:
        registry.register(TelegramChannel(env.telegram_bot_token, session_manager))

    static_dir = Path(__file__).resolve().parent / "static"

    st = AppState(
        config=app_config,
        env=env,
        db=db,
        hub=hub,
        registry=registry,
        session_manager=session_manager,
        static_dir=static_dir,
    )

    app = FastAPI(title="Cursor CLI Control Plane", lifespan=lifespan)
    app.state.control_plane = st

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api")

    @app.websocket("/ws")
    async def ws_route(websocket: WebSocket) -> None:
        # Parameter must be annotated as WebSocket; otherwise FastAPI treats "websocket" as a required query field.
        await websocket_endpoint(websocket)

    @app.get("/api")
    async def api_root():
        return {"message": "See /api/runs, /api/repos, WebSocket /ws"}

    # Do not mount StaticFiles at "/" — it intercepts WebSocket /ws and returns 403.
    if static_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(static_dir)), name="assets")

        @app.get("/")
        async def serve_dashboard() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    return app
