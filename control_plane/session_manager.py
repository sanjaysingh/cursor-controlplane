"""Long-lived conversational agent sessions (workspace + ACP until explicit close)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from control_plane.acp_client import AcpClient
from control_plane.config import AppConfig, EnvSettings
from control_plane.db import Database
from control_plane.events import EventHub
from control_plane.models import (
    AgentActivity,
    AgentSessionPublic,
    IncomingMessage,
    MessageTarget,
    utcnow,
)
from control_plane.channels.registry import ChannelRegistry

logger = logging.getLogger(__name__)

# Keys that appear on session/prompt RPC result when the model finished a turn without
# carrying the visible reply (the reply is streamed via session/update instead).
_PROMPT_RESULT_META_KEYS = frozenset(
    {"stopReason", "stop_reason", "reason", "usage", "model", "id", "sessionId", "session_id"}
)


def _text_from_session_prompt_result(result: Any) -> str:
    """
    ACP `session/prompt` often returns only turn metadata (e.g. stopReason).
    User-visible text should come from streamed session/update chunks (output_buffer).
    """
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if not isinstance(result, dict):
        return str(result).strip()
    for key in ("text", "message", "markdown"):
        v = result.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    inner = result.get("result")
    if isinstance(inner, str) and inner.strip():
        return inner.strip()
    if isinstance(inner, dict):
        t = inner.get("text") if isinstance(inner.get("text"), str) else None
        if t and t.strip():
            return t.strip()
    blocks = result.get("content")
    if isinstance(blocks, list):
        parts: list[str] = []
        for b in blocks:
            if isinstance(b, dict):
                t = b.get("text")
                if isinstance(t, str) and t:
                    parts.append(t)
        joined = "\n".join(parts).strip()
        if joined:
            return joined
    if set(result.keys()) <= _PROMPT_RESULT_META_KEYS:
        return ""
    return ""


def _normalize_session_model(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    if not s:
        return None
    from control_plane.model_cli import cli_model_id_for_argv

    return cli_model_id_for_argv(s)


def _extract_text_from_acp_update(
    update: dict[str, Any],
    *,
    mode: Literal["agent_message_chunk_only", "all"],
) -> str:
    """Extract assistant-visible text from session/update (see https://cursor.com/docs/cli/acp)."""
    chunk_type = update.get("sessionUpdate") or update.get("type")
    content = update.get("content")

    if mode == "agent_message_chunk_only":
        if chunk_type == "agent_message_chunk" and isinstance(content, dict):
            t = content.get("text")
            return str(t) if isinstance(t, str) else ""
        return ""

    # mode == "all": legacy broad extraction
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text", ""))
        delta = content.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("text"), str):
            return str(delta["text"])
    if chunk_type == "agent_message_chunk" and isinstance(content, dict):
        return str(content.get("text", ""))
    if isinstance(update.get("text"), str):
        return str(update["text"])
    if update.get("role") == "assistant" and isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


@dataclass
class ManagedAgentSession:
    id: str
    channel: str
    channel_key: str
    repo_path: str
    title: str
    status: str
    acp_session_id: str | None
    model: str | None
    created_at: str
    updated_at: str
    closed_at: str | None
    activity: AgentActivity = AgentActivity.idle
    error_message: str | None = None
    output_buffer: str = ""
    client: AcpClient | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @staticmethod
    def from_row(row: dict[str, Any]) -> ManagedAgentSession:
        return ManagedAgentSession(
            id=row["id"],
            channel=row["channel"],
            channel_key=row["channel_key"],
            repo_path=row["repo_path"],
            title=row.get("title") or "",
            status=row["status"],
            acp_session_id=row.get("acp_session_id"),
            model=_normalize_session_model(row.get("model")),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            closed_at=row.get("closed_at"),
        )


class SessionManager:
    def __init__(
        self,
        db: Database,
        app_config: AppConfig,
        env: EnvSettings,
        registry: ChannelRegistry,
        hub: EventHub,
    ) -> None:
        self.db = db
        self.app_config = app_config
        self.env = env
        self.registry = registry
        self.hub = hub
        self._managed: dict[str, ManagedAgentSession] = {}
        self._telegram_repo: dict[str, str] = {}
        self._telegram_active_session: dict[str, str] = {}
        self._session_create_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._stream_buffers: dict[str, str] = defaultdict(str)
        self._flush_tasks: dict[str, asyncio.Task[None] | None] = {}

    def set_telegram_repo(self, chat_id: str, repo_path: str) -> None:
        self._telegram_repo[str(chat_id)] = repo_path

    def get_telegram_repo(self, chat_id: str) -> str | None:
        return self._telegram_repo.get(str(chat_id))

    def set_telegram_active_session(self, chat_id: str, session_id: str | None) -> None:
        cid = str(chat_id)
        if session_id:
            self._telegram_active_session[cid] = session_id
        else:
            self._telegram_active_session.pop(cid, None)

    def get_telegram_active_session(self, chat_id: str) -> str | None:
        return self._telegram_active_session.get(str(chat_id))

    def _repo_name_for_path(self, path: str) -> str:
        for r in self.app_config.repos:
            if Path(r.path).resolve() == Path(path).resolve():
                return r.name
        return Path(path).name

    def _resolve_repo_path(self, msg: IncomingMessage) -> str | None:
        if msg.repo_path and Path(msg.repo_path).is_dir():
            return str(Path(msg.repo_path).resolve())
        if msg.channel == "telegram":
            p = self.get_telegram_repo(msg.conversation_id)
            if p and Path(p).is_dir():
                return str(Path(p).resolve())
        if self.app_config.repos:
            first = self.app_config.repos[0]
            if Path(first.path).is_dir():
                return str(Path(first.path).resolve())
        return None

    def _effective_model(self, ms: ManagedAgentSession) -> str | None:
        """Resolved `agent --model`: session → env → config (ids only, never `id - label`)."""
        from control_plane.model_cli import cli_model_id_for_argv

        if ms.model and ms.model.strip():
            mid = cli_model_id_for_argv(ms.model.strip())
            if mid is not None:
                return mid
            # Session stored a placeholder like `current` — treat as unset; use env/config.
        env_m = (self.env.cursor_agent_model or "").strip()
        if env_m:
            return cli_model_id_for_argv(env_m)
        cfg_m = (self.app_config.acp.default_model or "").strip()
        return cli_model_id_for_argv(cfg_m) if cfg_m else None

    async def ensure_managed(self, session_id: str) -> ManagedAgentSession:
        if session_id in self._managed:
            return self._managed[session_id]
        row = await self.db.get_agent_session(session_id)
        if not row:
            raise KeyError(session_id)
        ms = ManagedAgentSession.from_row(row)
        self._managed[session_id] = ms
        return ms

    def _to_public(self, ms: ManagedAgentSession) -> AgentSessionPublic:
        return AgentSessionPublic(
            id=ms.id,
            channel=ms.channel,
            channel_key=ms.channel_key,
            repo_path=ms.repo_path,
            repo_name=self._repo_name_for_path(ms.repo_path),
            title=ms.title,
            status=ms.status,
            activity=ms.activity.value,
            acp_session_id=ms.acp_session_id,
            model=ms.model,
            created_at=ms.created_at,
            updated_at=ms.updated_at,
            closed_at=ms.closed_at,
            error_message=ms.error_message,
            output_preview=(ms.output_buffer[-4000:] if ms.output_buffer else ""),
        )

    def _row_to_public(self, row: dict[str, Any], activity: AgentActivity = AgentActivity.idle) -> AgentSessionPublic:
        ms = ManagedAgentSession.from_row(row)
        ms.activity = activity
        return self._to_public(ms)

    async def _emit_session(self, ms: ManagedAgentSession, *, closed: bool = False) -> None:
        ev = {"type": "session_closed" if closed else "session_updated", "session": self._to_public(ms).to_public_dict()}
        await self.hub.publish(ev)

    async def create_session(
        self,
        channel: str,
        channel_key: str,
        repo_path: str,
        title: str = "",
        model: str | None = None,
    ) -> AgentSessionPublic:
        repo_path = str(Path(repo_path).resolve())
        sid = str(uuid.uuid4())
        m = _normalize_session_model(model)
        await self.db.insert_agent_session(sid, channel, channel_key, repo_path, title or "New chat", model=m)
        # insert_agent_session also ensures creator as session_participants row
        ms = await self.ensure_managed(sid)
        await self._emit_session(ms)
        return self._to_public(ms)

    async def join_session(
        self, session_id: str, channel: str, conversation_id: str
    ) -> AgentSessionPublic | None:
        """Attach a channel/conversation as a full participant (symmetric with creator)."""
        row = await self.db.get_agent_session(session_id)
        if not row:
            return None
        await self.db.ensure_session_participant(session_id, channel, conversation_id)
        ms = await self.ensure_managed(session_id)
        await self._emit_session(ms)
        return self._to_public(ms)

    async def list_sessions(
        self,
        channel: str,
        channel_key: str,
        *,
        include_closed: bool = True,
        limit: int = 100,
    ) -> list[AgentSessionPublic]:
        rows = await self.db.list_agent_sessions(channel, channel_key, include_closed=include_closed, limit=limit)
        out: list[AgentSessionPublic] = []
        for row in rows:
            sid = row["id"]
            if sid in self._managed:
                out.append(self._to_public(self._managed[sid]))
            else:
                out.append(self._row_to_public(row))
        return out

    async def list_all_sessions_global(
        self,
        *,
        include_closed: bool = True,
        limit: int = 100,
    ) -> list[AgentSessionPublic]:
        """All sessions across every channel (for Telegram cross-channel view)."""
        rows = await self.db.list_all_agent_sessions_global(include_closed=include_closed, limit=limit)
        out: list[AgentSessionPublic] = []
        for row in rows:
            sid = row["id"]
            if sid in self._managed:
                out.append(self._to_public(self._managed[sid]))
            else:
                out.append(self._row_to_public(row))
        return out

    async def get_session_public(self, session_id: str) -> AgentSessionPublic | None:
        try:
            ms = await self.ensure_managed(session_id)
            return self._to_public(ms)
        except KeyError:
            return None

    async def list_session_messages(self, session_id: str, limit: int = 500) -> list[dict[str, Any]]:
        return await self.db.list_session_messages(session_id, limit=limit)

    async def close_session(self, session_id: str) -> bool:
        try:
            ms = await self.ensure_managed(session_id)
        except KeyError:
            return False
        async with ms.lock:
            await self._cancel_all_pending_questions(session_id)
            if ms.client:
                try:
                    await ms.client.cancel_and_kill()
                except Exception as e:
                    logger.warning("close_session kill: %s", e)
                ms.client = None
            ms.status = "closed"
            ms.activity = AgentActivity.idle
            ms.closed_at = utcnow().isoformat()
            await self.db.close_agent_session_row(session_id)
            await self._emit_session(ms, closed=True)
            parts = await self._all_participants(ms)
            for ch_name, conv_id in parts:
                try:
                    ch = self.registry.get(ch_name)
                    await ch.send_message(conv_id, "Session closed. The Cursor agent process was stopped.")
                except Exception:
                    pass
        self._managed.pop(session_id, None)
        for k in list(self._stream_buffers.keys()):
            if k.startswith(f"{session_id}:"):
                self._stream_buffers.pop(k, None)
        for k in list(self._flush_tasks.keys()):
            if isinstance(k, str) and k.startswith(f"{session_id}:"):
                t = self._flush_tasks.pop(k, None)
                if t and not t.done():
                    t.cancel()
        return True

    async def close_all_sessions(self, channel: str, channel_key: str) -> int:
        """Close all open sessions this (channel, conversation_id) participates in."""
        targets = await self.db.list_session_ids_for_participant(channel, channel_key, open_only=True)
        count = 0
        for sid in targets:
            ok = await self.close_session(sid)
            if ok:
                count += 1
        return count

    async def close_all_open_sessions_globally(self) -> int:
        """Close every open session (single-tenant operator; all channels)."""
        rows = await self.db.list_all_open_sessions()
        count = 0
        for r in rows:
            if await self.close_session(r["id"]):
                count += 1
        return count

    async def purge_all_sessions(self) -> int:
        """Kill all live processes, delete all sessions + messages from DB. Returns count deleted."""
        for sid, ms in list(self._managed.items()):
            await self._cancel_all_pending_questions(sid)
            if ms.client:
                try:
                    await ms.client.cancel_and_kill()
                except Exception as e:
                    logger.warning("purge_all kill %s: %s", sid, e)
                ms.client = None
        self._managed.clear()
        self._flush_tasks.clear()
        self._stream_buffers.clear()
        n = await self.db.delete_all_sessions()
        await self.hub.publish({"type": "sessions_purged"})
        return n

    async def _cancel_all_pending_questions(self, session_id: str) -> None:
        from control_plane.channels.telegram_channel import TelegramChannel
        from control_plane.channels.web_channel import WebChannel

        try:
            chw = self.registry.get("web")
            if isinstance(chw, WebChannel):
                chw.cancel_pending_question(session_id)
        except Exception:
            logger.exception("cancel web question")
        try:
            cht = self.registry.get("telegram")
            if isinstance(cht, TelegramChannel):
                cht.cancel_pending_question_for_session(session_id)
        except Exception:
            logger.exception("cancel telegram question")

    async def answer_web_question(
        self, session_id: str, answer: str, conversation_id: str | None = None
    ) -> bool:
        from control_plane.channels.web_channel import WebChannel

        ch = self.registry.get("web")
        if isinstance(ch, WebChannel):
            return ch.submit_answer(session_id, answer, conversation_id)
        return False

    async def submit_incoming(self, msg: IncomingMessage) -> AgentSessionPublic | None:
        # Telegram: honour explicit session pin set via /sessions connect button.
        if msg.channel == "telegram":
            pinned = self.get_telegram_active_session(msg.conversation_id)
            if pinned:
                row = await self.db.get_agent_session(pinned)
                if row:
                    await self.db.ensure_session_participant(pinned, msg.channel, msg.conversation_id)
                    await self.send_session_message(
                        pinned,
                        msg.text,
                        participant_channel=msg.channel,
                        participant_conversation_id=msg.conversation_id,
                    )
                    ms = await self.ensure_managed(pinned)
                    return self._to_public(ms)
                # Pinned session no longer exists — clear and fall through.
                self.set_telegram_active_session(msg.conversation_id, None)

        repo_path = self._resolve_repo_path(msg)
        if not repo_path:
            ch = self.registry.get(msg.channel)
            await ch.send_message(
                msg.conversation_id,
                "No valid repo path. Set `/repo <path>` (Telegram) or configure repos in config.yaml.",
            )
            return None

        lock_key = f"{msg.channel}:{msg.conversation_id}:{repo_path}"
        async with self._session_create_locks[lock_key]:
            row = await self.db.find_open_agent_session(msg.channel, msg.conversation_id, repo_path)
            if row:
                session_id = row["id"]
            else:
                pub = await self.create_session(msg.channel, msg.conversation_id, repo_path, "")
                session_id = pub.id

        await self.db.ensure_session_participant(session_id, msg.channel, msg.conversation_id)
        await self.send_session_message(
            session_id,
            msg.text,
            participant_channel=msg.channel,
            participant_conversation_id=msg.conversation_id,
        )
        ms = await self.ensure_managed(session_id)
        return self._to_public(ms)

    async def send_session_message(
        self,
        session_id: str,
        text: str,
        *,
        participant_channel: str | None = None,
        participant_conversation_id: str | None = None,
    ) -> AgentSessionPublic:
        ms = await self.ensure_managed(session_id)
        if participant_channel and participant_conversation_id:
            await self.db.ensure_session_participant(
                session_id, participant_channel, participant_conversation_id
            )
        async with ms.lock:
            if ms.status == "closed":
                await self.db.reopen_agent_session_row(session_id)
                ms.status = "open"
                ms.closed_at = None
                ms.error_message = None

            await self._ensure_acp(ms)

            if (not ms.title or ms.title == "New chat") and text.strip():
                snippet = text.strip()[:80] + ("…" if len(text.strip()) > 80 else "")
                await self.db.update_agent_session_title(session_id, snippet)
                ms.title = snippet

            await self.db.append_session_message(session_id, "user", text)
            ms.activity = AgentActivity.running
            ms.output_buffer = ""
            await self._emit_session(ms)

            try:
                result = await ms.client.session_prompt(text)  # type: ignore[union-attr]
                await self._flush_all_stream_buffers(ms)
                streamed = (ms.output_buffer or "").strip()
                result_text = _text_from_session_prompt_result(result)
                summary = streamed or result_text
                if summary:
                    await self.db.append_session_message(session_id, "assistant", summary[:20000])
                # If there was no streaming output, the result text was never sent to anyone — broadcast it now.
                if not streamed and result_text:
                    await self._broadcast_to_all(ms, result_text)
                ms.error_message = None
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("session_prompt failed")
                ms.error_message = str(e)
                ms.activity = AgentActivity.error
                await self._emit_session(ms)
                parts = await self._all_participants(ms)
                for ch_name, conv_id in parts:
                    try:
                        ch = self.registry.get(ch_name)
                        await ch.send_message(conv_id, f"Agent error: {e}")
                    except Exception:
                        pass
                raise
            finally:
                if ms.activity == AgentActivity.running:
                    ms.activity = AgentActivity.idle
                await self._emit_session(ms)

        return self._to_public(ms)

    async def _flush_all_stream_buffers(self, ms: ManagedAgentSession) -> None:
        """Flush buffered non-web stream chunks after a prompt turn (cancel timers, send immediately)."""
        parts = await self._all_participants(ms)
        for ch_name, conv_id in parts:
            if ch_name == "web":
                continue
            key = f"{ms.id}:{ch_name}:{conv_id}"
            task = self._flush_tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
            buf = self._stream_buffers.pop(key, "")
            if buf:
                try:
                    ch = self.registry.get(ch_name)
                    await ch.send_message(conv_id, buf)
                except Exception as e:
                    logger.warning("stream flush %s/%s failed: %s", ch_name, conv_id, e)

    async def _broadcast_to_all(self, ms: ManagedAgentSession, text: str) -> None:
        """Send text to every participant (used when no streaming happened)."""
        for ch_name, conv_id in await self._all_participants(ms):
            try:
                ch = self.registry.get(ch_name)
                await ch.send_message(conv_id, text)
            except Exception as e:
                logger.warning("broadcast to %s/%s failed: %s", ch_name, conv_id, e)

    async def _set_acp_model(self, client: AcpClient, want_cli_id: str) -> None:
        """Set the session model via ACP session/set_config_option after session/new."""
        from control_plane.acp_model_probe import (
            _find_model_config_option,
            _dropdown_label_for_acp_model_value,
        )

        config_opts = client.session_config_options
        if not config_opts:
            logger.debug("_set_acp_model: no configOptions from session/new, skipping model set")
            return

        model_opt = _find_model_config_option(config_opts)
        if not model_opt:
            logger.debug("_set_acp_model: no model config option in configOptions")
            return

        config_id = model_opt.get("id") or "model"
        options = model_opt.get("options") or []
        want_lower = want_cli_id.lower().strip()
        best_value: str | None = None

        for opt in options:
            if not isinstance(opt, dict):
                continue
            acp_val = str(opt.get("value") or "").strip()
            if not acp_val:
                continue
            label = _dropdown_label_for_acp_model_value(acp_val).lower()
            if label == want_lower or acp_val.lower() == want_lower:
                best_value = acp_val
                break

        if best_value is None:
            logger.warning(
                "_set_acp_model: CLI model id %r not matched in ACP configOptions; available: %s",
                want_cli_id,
                [str(o.get("value", "")) for o in options if isinstance(o, dict)],
            )
            return

        try:
            await client.session_set_config_option(config_id, best_value)
            logger.info(
                "_set_acp_model: set model via ACP configOption %s=%r (wanted CLI id: %r)",
                config_id,
                best_value,
                want_cli_id,
            )
        except Exception as e:
            logger.warning("_set_acp_model: session/set_config_option failed: %s", e)

    async def _ensure_acp(self, ms: ManagedAgentSession) -> None:
        want = self._effective_model(ms)
        proc = ms.client._proc if ms.client else None  # type: ignore[attr-defined]
        alive = proc is not None and proc.returncode is None
        if ms.client and alive:
            if getattr(ms.client, "spawn_model", None) != want:
                try:
                    await self._cancel_all_pending_questions(ms.id)
                    await ms.client.cancel_and_kill()
                except Exception as e:
                    logger.warning("_ensure_acp recycle for model change: %s", e)
                ms.client = None
            else:
                return

        if ms.client:
            try:
                await ms.client.kill()
            except Exception:
                pass
            ms.client = None

        ms.activity = AgentActivity.connecting
        await self._emit_session(ms)

        async def on_update(params: dict[str, Any]) -> None:
            await self._on_acp_update(ms, params)

        async def on_permission(req_id: str, params: dict[str, Any]) -> dict[str, Any]:
            return await self._on_permission(ms, req_id, params)

        async def on_question(req_id: str, msg: dict[str, Any]) -> dict[str, Any]:
            return await self._on_question(ms, req_id, msg)

        agent_bin = self.env.cursor_agent_bin or self.app_config.acp.command
        extra = list(self.app_config.acp.extra_args)
        api_key = self.env.cursor_api_key or None

        client = AcpClient(
            workspace=ms.repo_path,
            agent_executable=agent_bin,
            extra_args=extra,
            api_key=api_key,
            on_update=on_update,
            on_permission=on_permission,
            on_question=on_question,
            model=want,
        )
        ms.client = client

        try:
            await client.start()
            await client.initialize()
            await client.authenticate()
            if ms.acp_session_id:
                try:
                    await client.session_load(ms.acp_session_id)
                except Exception as e:
                    logger.warning("session_load failed, new session: %s", e)
                    await client.session_new()
                    ms.acp_session_id = client.session_id
                    await self.db.update_agent_session_acp(ms.id, ms.acp_session_id)
                    if want:
                        await self._set_acp_model(client, want)
            else:
                await client.session_new()
                ms.acp_session_id = client.session_id
                await self.db.update_agent_session_acp(ms.id, ms.acp_session_id)
                if want:
                    await self._set_acp_model(client, want)
        except Exception:
            ms.client = None
            raise

        ms.activity = AgentActivity.idle
        await self._emit_session(ms)

    async def _all_participants(self, ms: ManagedAgentSession) -> list[tuple[str, str]]:
        return await self.db.list_session_participants(ms.id)

    async def _on_acp_update(self, ms: ManagedAgentSession, params: dict[str, Any]) -> None:
        update = params.get("update") or params
        if not isinstance(update, dict):
            return
        text = _extract_text_from_acp_update(
            update,
            mode=self.app_config.acp.stream_update_mode,
        )
        if text:
            ms.output_buffer += text
            await self.db.touch_agent_session(ms.id)
            await self._emit_session(ms)
            subs = await self._all_participants(ms)
            for ch_name, conv_id in subs:
                if ch_name == "web":
                    await self.hub.publish(
                        {
                            "type": "agent_stream",
                            "session_id": ms.id,
                            "conversation_id": conv_id,
                            "text": text,
                        }
                    )
                else:
                    key = f"{ms.id}:{ch_name}:{conv_id}"
                    self._stream_buffers[key] += text
                    self._schedule_stream_flush_for(ms, ch_name, conv_id, delay=1.0)

    def _schedule_stream_flush_for(
        self, ms: ManagedAgentSession, ch_name: str, conv_id: str, delay: float = 1.0
    ) -> None:
        key = f"{ms.id}:{ch_name}:{conv_id}"
        prev = self._flush_tasks.get(key)
        if prev and not prev.done():
            prev.cancel()

        async def _delayed() -> None:
            await asyncio.sleep(delay)
            buf = self._stream_buffers.pop(key, "")
            if buf:
                try:
                    ch = self.registry.get(ch_name)
                    await ch.send_message(conv_id, buf)
                except Exception as e:
                    logger.warning("stream flush to %s/%s failed: %s", ch_name, conv_id, e)

        self._flush_tasks[key] = asyncio.create_task(_delayed())

    async def _on_permission(self, _ms: ManagedAgentSession, _req_id: str, _params: dict[str, Any]) -> dict[str, Any]:
        return {"outcome": {"outcome": "selected", "optionId": "allow-once"}}

    async def _on_question(
        self,
        ms: ManagedAgentSession,
        _req_id: str,
        msg: dict[str, Any],
    ) -> dict[str, Any]:
        params = msg.get("params") or {}
        question = str(params.get("question") or params.get("title") or msg.get("method") or "Confirm?")
        options = params.get("options") or params.get("choices") or []
        if isinstance(options, list) and options:
            opts = [str(o.get("label", o)) if isinstance(o, dict) else str(o) for o in options]
        else:
            opts = ["OK"]
        prev = ms.activity
        ms.activity = AgentActivity.waiting_user
        await self._emit_session(ms)

        subs = await self._all_participants(ms)
        answer = opts[0] if opts else ""
        tasks: list[asyncio.Task[str]] = []
        try:
            if subs:
                for ch_name, conv_id in subs:
                    ch = self.registry.get(ch_name)
                    mt = MessageTarget(session_id=ms.id, conversation_id=conv_id)
                    tasks.append(
                        asyncio.create_task(ch.ask_question(conv_id, question, opts, mt))
                    )
                done, pend = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                first = next(iter(done))
                try:
                    answer = first.result()
                except Exception as e:
                    logger.warning("ask_question task error: %s", e)
                    answer = opts[0] if opts else ""
                for t in pend:
                    t.cancel()
                await asyncio.gather(*pend, return_exceptions=True)
            await self._cancel_all_pending_questions(ms.id)
        finally:
            ms.activity = AgentActivity.running if prev == AgentActivity.running else AgentActivity.idle
            await self._emit_session(ms)

        for ch_name, conv_id in subs:
            try:
                ch = self.registry.get(ch_name)
                await ch.send_message(conv_id, f"✅ Answered: {answer}")
            except Exception as e:
                logger.warning("answer notify to %s/%s failed: %s", ch_name, conv_id, e)

        if isinstance(options, list) and options and all(isinstance(o, dict) and "id" in o for o in options):
            for o in options:
                if str(o.get("label")) == answer or str(o.get("id")) == answer:
                    return {"responses": [{"optionId": o.get("id")}]}
        return {"responses": [{"optionId": answer}]}

    # --- Legacy /api/runs compatibility (session id == former run id) ---
    async def legacy_create_run(self, conversation_id: str, repo_path: str, prompt: str) -> AgentSessionPublic | None:
        msg = IncomingMessage(conversation_id=conversation_id, channel="web", text=prompt, repo_path=repo_path)
        return await self.submit_incoming(msg)

    async def stop_run(self, session_id: str) -> bool:
        """Alias for close_session (same UUID identifies the session)."""
        return await self.close_session(session_id)
