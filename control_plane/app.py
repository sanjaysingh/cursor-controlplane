"""FastAPI application factory."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from control_plane.api.routes import router as api_router, websocket_endpoint
from control_plane.config import get_settings, parse_telegram_allowed_user_ids
from control_plane.db import Database
from control_plane.paths import database_path, static_package_dir
from control_plane.events import EventHub
from control_plane.channels.registry import ChannelRegistry
from control_plane.channels.web_channel import WebChannel
from control_plane.channels.telegram_channel import TelegramChannel
from control_plane.session_manager import SessionManager
from control_plane.state import AppState
from control_plane.workspace_paths import resolve_workspace_root

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_RETENTION_DAYS = 7


class DailyFileHandler(logging.Handler):
    """Write logs into one file per day and prune old files."""

    def __init__(self, base_path: Path, *, retention_days: int = _LOG_RETENTION_DAYS) -> None:
        super().__init__()
        self.base_path = base_path.expanduser().resolve()
        self.retention_days = retention_days
        self._current_day: date | None = None
        self._last_cleanup_day: date | None = None
        self._file_handler: logging.FileHandler | None = None
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_handler(datetime.now())
        self._cleanup_old_logs(datetime.now())

    def _daily_path(self, day: date) -> Path:
        stem = self.base_path.stem
        suffix = self.base_path.suffix
        return self.base_path.with_name(f"{stem}-{day.isoformat()}{suffix}")

    def _is_managed_log_file(self, path: Path) -> bool:
        if path.parent != self.base_path.parent or not path.is_file():
            return False
        stem = self.base_path.stem
        suffix = self.base_path.suffix
        prefix = f"{stem}-"
        return path.name.startswith(prefix) and path.name.endswith(suffix)

    def _ensure_handler(self, now: datetime) -> None:
        today = now.date()
        if self._current_day == today and self._file_handler:
            return
        if self._file_handler:
            self._file_handler.close()
        self._current_day = today
        fh = logging.FileHandler(self._daily_path(today), encoding="utf-8")
        fh.setFormatter(self.formatter or logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
        self._file_handler = fh

    def _cleanup_old_logs(self, now: datetime) -> None:
        if self._last_cleanup_day == now.date():
            return
        self._last_cleanup_day = now.date()
        cutoff = now - timedelta(days=self.retention_days)
        for candidate in self.base_path.parent.iterdir():
            if not self._is_managed_log_file(candidate):
                continue
            try:
                if datetime.fromtimestamp(candidate.stat().st_mtime) < cutoff:
                    candidate.unlink()
            except OSError:
                continue

    def setFormatter(self, fmt: logging.Formatter) -> None:  # noqa: N802 - stdlib API
        super().setFormatter(fmt)
        if self._file_handler:
            self._file_handler.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        now = datetime.now()
        self._ensure_handler(now)
        self._cleanup_old_logs(now)
        if self._file_handler:
            self._file_handler.emit(record)

    def close(self) -> None:
        try:
            if self._file_handler:
                self._file_handler.close()
                self._file_handler = None
        finally:
            super().close()


def _attach_log_file(path: Path) -> None:
    """Append a daily rotating UTF-8 file handler to the root logger."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, DailyFileHandler) and str(h.base_path) == resolved:
            return
    fh = DailyFileHandler(path)
    fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    root.addHandler(fh)


@asynccontextmanager
async def lifespan(app: FastAPI):
    st: AppState = app.state.control_plane
    await st.db.init_schema()
    for r in st.config.repos:
        await st.db.upsert_repo(r.name, r.path, r.description)

    await st.session_manager.refresh_db_default_model()

    wr = resolve_workspace_root(st.config, st.env)
    wr.mkdir(parents=True, exist_ok=True)
    logger.info("Workspace root: %s", wr)

    await st.registry.get("web").start()
    tg = st.registry.all().get("telegram")
    if tg:
        try:
            await tg.start()
            logger.info("Telegram channel started")
        except Exception:
            logger.exception("Telegram channel failed to start; continuing without Telegram")
    else:
        logger.info("Telegram channel disabled or missing token")

    yield

    for ch in st.registry.all().values():
        try:
            await ch.stop()
        except Exception:
            logger.exception("Channel stop failed")


def create_app() -> FastAPI:
    app_config, env = get_settings()
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)
    if (env.log_file or "").strip():
        _attach_log_file(Path(env.log_file.strip()))
    hub = EventHub()
    registry = ChannelRegistry()
    db = Database(database_path())
    session_manager = SessionManager(db, app_config, env, registry, hub)
    web = WebChannel(hub)
    registry.register(web)
    if app_config.channels.telegram.get("enabled") and env.telegram_bot_token:
        allowed_ids = parse_telegram_allowed_user_ids(env.telegram_allowed_user_ids)
        if not allowed_ids:
            logger.info(
                "Telegram is enabled but TELEGRAM_ALLOWED_USER_IDS is empty — "
                "no slash-command menu and every update will be ignored until you set at least one user id."
            )
        registry.register(TelegramChannel(env.telegram_bot_token, session_manager, allowed_ids))

    static_dir = static_package_dir()

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
