"""Telegram bot channel (aiogram 3)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from control_plane.channels.base import BaseChannel
from control_plane.models import IncomingMessage, MessageTarget
from control_plane.session_manager import SessionManager

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self, token: str, session_manager: SessionManager) -> None:
        self._token = token
        self._sm = session_manager
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None
        self._task: asyncio.Task[None] | None = None
        # callback token -> (future, options, session_id) for cancel + first-answer wins across chats
        self._pending_question: dict[str, tuple[asyncio.Future[str], list[str], str]] = {}

    @staticmethod
    def _question_cb_token(session_id: str, conversation_id: str) -> str:
        return hashlib.sha256(f"{session_id}:{conversation_id}".encode()).hexdigest()[:12]

    def _session_summary(self, s: Any) -> str:
        title = (s.title or s.repo_name or "")[:35]
        status = "🟢" if s.status == "open" else "⚫"
        model = f" [{s.model}]" if s.model else ""
        return f"{status} {title or s.id[:8]}{model}"

    async def start(self) -> None:
        self._bot = Bot(self._token)
        self._dp = Dispatcher()
        dp = self._dp
        sm = self._sm

        @dp.message(CommandStart())
        async def cmd_start(message: Message) -> None:
            await message.answer(
                "Cursor Control Plane.\n"
                "/repo <path> — set workspace\n"
                "/sessions — list & connect to any session\n"
                "Send text — continues the connected/active session.\n"
                "/session close — stop current agent · /session new — start fresh"
            )

        @dp.message(Command("repos"))
        async def cmd_repos(message: Message) -> None:
            lines = []
            for r in sm.app_config.repos:
                lines.append(f"• {r.name}: {r.path}")
            text = "\n".join(lines) if lines else "No repos in config.yaml — add under repos:"
            await message.answer(text)

        @dp.message(Command("repo"))
        async def cmd_repo(message: Message) -> None:
            assert message.text and message.chat
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2:
                await message.answer("Usage: /repo C:/path/to/repo")
                return
            raw = parts[1].strip().strip('"')
            path = Path(raw)
            if not path.is_dir():
                await message.answer(f"Not a directory: {raw}")
                return
            sm.set_telegram_repo(str(message.chat.id), str(path.resolve()))
            await message.answer(f"Repo set to {path.resolve()}")

        @dp.message(Command("status"))
        async def cmd_status(message: Message) -> None:
            assert message.chat
            chat_id = str(message.chat.id)
            sessions = await sm.list_all_sessions_global(include_closed=False, limit=20)
            if not sessions:
                await message.answer("No open sessions.")
                return
            active = sm.get_telegram_active_session(chat_id)
            lines = []
            for s in sessions:
                marker = "▶ " if s.id == active else "• "
                ch = f"[{s.channel}]" if s.channel != "telegram" else ""
                lines.append(f"{marker}{s.id[:8]}… {s.activity} {ch}— {s.title[:45]}")
            await message.answer("\n".join(lines))

        @dp.message(Command("sessions"))
        async def cmd_sessions(message: Message) -> None:
            assert message.chat
            chat_id = str(message.chat.id)
            sessions = await sm.list_all_sessions_global(include_closed=True, limit=20)
            if not sessions:
                await message.answer("No sessions yet.")
                return
            active = sm.get_telegram_active_session(chat_id)
            buttons: list[list[InlineKeyboardButton]] = []
            lines: list[str] = []
            for s in sessions:
                marker = "▶ " if s.id == active else ""
                label = f"{marker}{self._session_summary(s)}"
                lines.append(f"{label}  ({s.activity})")
                cb = f"sess:{s.id}"
                if len(cb) <= 64:
                    buttons.append([InlineKeyboardButton(text=label[:62], callback_data=cb)])
            kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
            await message.answer("Sessions — tap to connect:\n" + "\n".join(lines), reply_markup=kb)

        @dp.message(Command("session"))
        async def cmd_session(message: Message) -> None:
            assert message.text and message.chat
            parts = message.text.split(maxsplit=1)
            sub = (parts[1] if len(parts) > 1 else "").strip().lower()
            chat_id = str(message.chat.id)
            repo = sm.get_telegram_repo(chat_id)

            if sub == "close":
                active = sm.get_telegram_active_session(chat_id)
                if active:
                    await sm.close_session(active)
                    sm.set_telegram_active_session(chat_id, None)
                    await message.answer("Connected session closed.")
                    return
                if not repo:
                    await message.answer("Set /repo first or use /sessions to connect.")
                    return
                repo_resolved = str(Path(repo).resolve())
                row = await sm.db.find_open_agent_session("telegram", chat_id, repo_resolved)
                if not row:
                    await message.answer("No open session for this repo.")
                    return
                await sm.close_session(row["id"])
                await message.answer("Session closed (agent process stopped).")
                return

            if sub == "new":
                active = sm.get_telegram_active_session(chat_id)
                if active:
                    await sm.close_session(active)
                    sm.set_telegram_active_session(chat_id, None)
                if repo:
                    repo_resolved = str(Path(repo).resolve())
                    row = await sm.db.find_open_agent_session("telegram", chat_id, repo_resolved)
                    if row:
                        await sm.close_session(row["id"])
                await message.answer("Previous session closed. Your next message starts a new session.")
                return

            # bare /session → redirect to /sessions
            await cmd_sessions(message)

        @dp.callback_query(F.data.startswith("sess:"))
        async def on_session_connect(cb: CallbackQuery) -> None:
            data = cb.data or ""
            session_id = data[5:]  # strip "sess:"
            if not session_id or not cb.message:
                await cb.answer()
                return
            chat_id = str(cb.message.chat.id)
            row = await sm.db.get_agent_session(session_id)
            if not row:
                await cb.answer("Session not found.", show_alert=True)
                return
            await sm.db.ensure_session_participant(session_id, "telegram", chat_id)
            sm.set_telegram_active_session(chat_id, session_id)
            title = row.get("title") or session_id[:8]
            status = row.get("status", "")
            await cb.answer(f"Connected to: {title[:40]}")
            await cb.message.answer(
                f"▶ Connected to session: {title}\n"
                f"Status: {status} — your messages now go to this session.\n"
                f"Use /session new to disconnect and start fresh."
            )

        @dp.callback_query(F.data.startswith("q:"))
        async def on_answer(cb: CallbackQuery) -> None:
            data = cb.data or ""
            parts = data.split(":")
            if len(parts) != 3:
                await cb.answer()
                return
            _, token, idx_s = parts
            try:
                idx = int(idx_s)
            except ValueError:
                await cb.answer()
                return
            entry = self._pending_question.pop(token, None)
            if not entry:
                await cb.answer()
                return
            fut, opts, _sid = entry
            if fut.done():
                await cb.answer()
                return
            label = opts[idx] if 0 <= idx < len(opts) else ""
            fut.set_result(label)
            await cb.answer()

        @dp.message(F.text & ~F.text.startswith("/"))
        async def on_text(message: Message) -> None:
            assert message.text and message.chat
            await sm.submit_incoming(
                IncomingMessage(
                    conversation_id=str(message.chat.id),
                    channel=self.name,
                    text=message.text,
                )
            )

        assert self._bot
        await self._bot.set_my_commands([
            BotCommand(command="start",    description="Show help"),
            BotCommand(command="sessions", description="List all sessions & connect"),
            BotCommand(command="session",  description="session close | new"),
            BotCommand(command="status",   description="Show open sessions"),
            BotCommand(command="repos",    description="List configured repos"),
            BotCommand(command="repo",     description="Set workspace path"),
        ])
        self._task = asyncio.create_task(dp.start_polling(self._bot))

    async def stop(self) -> None:
        if self._dp and self._bot:
            try:
                await self._dp.stop_polling()
            except RuntimeError as e:
                if "Polling is not started" not in str(e):
                    raise
            await self._bot.session.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._bot = None
        self._dp = None

    async def send_message(self, conversation_id: str, text: str) -> None:
        if not self._bot:
            return
        chat_id = int(conversation_id)
        chunk = text[:4000] if text else ""
        if chunk:
            await self._bot.send_message(chat_id, chunk)

    async def ask_question(
        self,
        conversation_id: str,
        question: str,
        options: list[str],
        target: MessageTarget,
    ) -> str:
        if not self._bot:
            return options[0] if options else ""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        token = self._question_cb_token(target.session_id, conversation_id)
        self._pending_question[token] = (fut, list(options), target.session_id)
        buttons = []
        row = []
        for i, opt in enumerate(options):
            cb_data = f"q:{token}:{i}"
            if len(cb_data) > 64:
                logger.warning("callback_data too long, truncating options")
                break
            row.append(InlineKeyboardButton(text=opt[:40], callback_data=cb_data))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        chat_id = int(conversation_id)
        await self._bot.send_message(chat_id, question[:3500], reply_markup=kb)
        try:
            return await asyncio.wait_for(fut, timeout=3600.0)
        except asyncio.TimeoutError:
            return options[0] if options else ""
        finally:
            self._pending_question.pop(token, None)

    def cancel_pending_question_for_session(self, session_id: str) -> None:
        for t, entry in list(self._pending_question.items()):
            fut, opts, sid = entry
            if sid == session_id:
                self._pending_question.pop(t, None)
                if not fut.done():
                    fut.set_result(opts[0] if opts else "")
