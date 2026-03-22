"""SQLite persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

# Legacy tables kept for existing DB files; new code uses agent_sessions + session_messages.
SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    channel_type TEXT NOT NULL,
    channel_conversation_id TEXT NOT NULL,
    repo_path TEXT NOT NULL DEFAULT '',
    acp_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_conv_channel_repo
ON conversations(channel_type, channel_conversation_id, repo_path);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    acp_session_id TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_ck ON agent_sessions(channel, channel_key);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions(status);

CREATE TABLE IF NOT EXISTS session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_messages_sid ON session_messages(session_id);

CREATE TABLE IF NOT EXISTS session_participants (
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (session_id, channel, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_session_participants_conv ON session_participants(channel, conversation_id);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def init_schema(self) -> None:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.executescript(SCHEMA)
            await self._add_column_if_missing(conn, "agent_sessions", "model", "TEXT")
            await conn.commit()
        await self._migrate_session_participants()

    async def _migrate_session_participants(self) -> None:
        """Create session_participants table on old DBs and backfill from agent_sessions."""
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_participants (
                    session_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, channel, conversation_id)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_participants_conv
                ON session_participants(channel, conversation_id)
                """
            )
            await conn.execute(
                """
                INSERT OR IGNORE INTO session_participants (session_id, channel, conversation_id, joined_at)
                SELECT id, channel, channel_key, created_at FROM agent_sessions
                """
            )
            await conn.commit()

    @staticmethod
    async def _add_column_if_missing(conn: Any, table: str, column: str, decl: str) -> None:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        names = {r[1] for r in rows}
        if column not in names:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    async def upsert_repo(self, name: str, path: str, description: str = "") -> None:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                INSERT INTO repos (name, path, description) VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET path=excluded.path, description=excluded.description
                """,
                (name, path, description),
            )
            await conn.commit()

    # --- Agent sessions (conversational) ---

    async def insert_agent_session(
        self,
        session_id: str,
        channel: str,
        channel_key: str,
        repo_path: str,
        title: str = "",
        model: str | None = None,
    ) -> None:
        from control_plane.models import utcnow

        now = utcnow().isoformat()
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                INSERT INTO agent_sessions (id, channel, channel_key, repo_path, title, status, acp_session_id, model, created_at, updated_at, closed_at)
                VALUES (?, ?, ?, ?, ?, 'open', NULL, ?, ?, ?, NULL)
                """,
                (session_id, channel, channel_key, repo_path, title or "New chat", model, now, now),
            )
            await conn.commit()
        await self.ensure_session_participant(session_id, channel, channel_key)

    async def ensure_session_participant(
        self, session_id: str, channel: str, conversation_id: str
    ) -> None:
        from control_plane.models import utcnow

        now = utcnow().isoformat()
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                INSERT INTO session_participants (session_id, channel, conversation_id, joined_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, channel, conversation_id) DO NOTHING
                """,
                (session_id, channel, conversation_id, now),
            )
            await conn.commit()

    async def list_session_participants(self, session_id: str) -> list[tuple[str, str]]:
        """Return (channel, conversation_id) sorted for stable ordering."""
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT channel, conversation_id FROM session_participants
                WHERE session_id = ?
                ORDER BY channel ASC, conversation_id ASC
                """,
                (session_id,),
            )
            rows = await cur.fetchall()
            return [(str(r["channel"]), str(r["conversation_id"])) for r in rows]

    async def list_session_ids_for_participant(
        self, channel: str, conversation_id: str, *, open_only: bool = True
    ) -> list[str]:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            if open_only:
                cur = await conn.execute(
                    """
                    SELECT DISTINCT s.id FROM agent_sessions s
                    INNER JOIN session_participants p ON p.session_id = s.id
                    WHERE p.channel = ? AND p.conversation_id = ? AND s.status = 'open'
                    ORDER BY s.updated_at DESC
                    """,
                    (channel, conversation_id),
                )
            else:
                cur = await conn.execute(
                    """
                    SELECT DISTINCT s.id FROM agent_sessions s
                    INNER JOIN session_participants p ON p.session_id = s.id
                    WHERE p.channel = ? AND p.conversation_id = ?
                    ORDER BY s.updated_at DESC
                    """,
                    (channel, conversation_id),
                )
            rows = await cur.fetchall()
            return [str(r["id"]) for r in rows]

    async def get_agent_session(self, session_id: str) -> dict[str, Any] | None:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_agent_sessions(
        self,
        channel: str,
        channel_key: str,
        *,
        include_closed: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            if include_closed:
                cur = await conn.execute(
                    """
                    SELECT s.* FROM agent_sessions s
                    INNER JOIN session_participants p ON p.session_id = s.id
                    WHERE p.channel = ? AND p.conversation_id = ?
                    ORDER BY
                      CASE WHEN s.status = 'open' THEN 0 ELSE 1 END,
                      s.updated_at DESC
                    LIMIT ?
                    """,
                    (channel, channel_key, limit),
                )
            else:
                cur = await conn.execute(
                    """
                    SELECT s.* FROM agent_sessions s
                    INNER JOIN session_participants p ON p.session_id = s.id
                    WHERE p.channel = ? AND p.conversation_id = ? AND s.status = 'open'
                    ORDER BY s.updated_at DESC
                    LIMIT ?
                    """,
                    (channel, channel_key, limit),
                )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def list_all_agent_sessions_global(
        self,
        *,
        include_closed: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List sessions across all channels (used by Telegram /sessions)."""
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            if include_closed:
                cur = await conn.execute(
                    """
                    SELECT * FROM agent_sessions
                    ORDER BY
                      CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                      updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            else:
                cur = await conn.execute(
                    """
                    SELECT * FROM agent_sessions
                    WHERE status = 'open'
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def list_all_open_sessions(self) -> list[dict[str, Any]]:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM agent_sessions WHERE status = 'open' ORDER BY updated_at DESC"
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def count_agent_sessions(self) -> int:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM agent_sessions")
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def delete_agent_session(self, session_id: str) -> bool:
        """Remove one session and its messages/participants. Returns True if the session existed."""
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("SELECT 1 FROM agent_sessions WHERE id = ?", (session_id,))
            if await cur.fetchone() is None:
                return False
            await conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
            await conn.execute("DELETE FROM session_participants WHERE session_id = ?", (session_id,))
            await conn.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
            await conn.commit()
            return True

    async def delete_all_sessions(self) -> int:
        """Hard-delete all session rows and their messages. Returns count deleted."""
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT COUNT(*) FROM agent_sessions")
            row = await cur.fetchone()
            n = row[0] if row else 0
            await conn.execute("DELETE FROM session_messages")
            await conn.execute("DELETE FROM session_participants")
            await conn.execute("DELETE FROM agent_sessions")
            await conn.commit()
            return n

    async def find_open_agent_session(
        self, channel: str, channel_key: str, repo_path: str
    ) -> dict[str, Any] | None:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT s.* FROM agent_sessions s
                INNER JOIN session_participants p ON p.session_id = s.id
                WHERE p.channel = ? AND p.conversation_id = ? AND s.repo_path = ? AND s.status = 'open'
                ORDER BY s.updated_at DESC LIMIT 1
                """,
                (channel, channel_key, repo_path),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_agent_session_acp(self, session_id: str, acp_session_id: str | None) -> None:
        from control_plane.models import utcnow

        now = utcnow().isoformat()
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                "UPDATE agent_sessions SET acp_session_id = ?, updated_at = ? WHERE id = ?",
                (acp_session_id, now, session_id),
            )
            await conn.commit()

    async def update_agent_session_title(self, session_id: str, title: str) -> None:
        from control_plane.models import utcnow

        now = utcnow().isoformat()
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                "UPDATE agent_sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, session_id),
            )
            await conn.commit()

    async def touch_agent_session(self, session_id: str) -> None:
        from control_plane.models import utcnow

        now = utcnow().isoformat()
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                "UPDATE agent_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            await conn.commit()

    async def close_agent_session_row(self, session_id: str) -> None:
        from control_plane.models import utcnow

        now = utcnow().isoformat()
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                UPDATE agent_sessions SET status = 'closed', closed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, session_id),
            )
            await conn.commit()

    async def reopen_agent_session_row(self, session_id: str) -> None:
        from control_plane.models import utcnow

        now = utcnow().isoformat()
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                UPDATE agent_sessions SET status = 'open', closed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, session_id),
            )
            await conn.commit()

    async def append_session_message(self, session_id: str, role: str, content: str) -> None:
        from control_plane.models import utcnow

        now = utcnow().isoformat()
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                "INSERT INTO session_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            )
            await conn.execute(
                "UPDATE agent_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            await conn.commit()

    async def list_session_messages(self, session_id: str, limit: int = 500) -> list[dict[str, Any]]:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT id, role, content, created_at FROM session_messages
                WHERE session_id = ? ORDER BY id ASC LIMIT ?
                """,
                (session_id, limit),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_setting(self, key: str) -> str | None:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
            row = await cur.fetchone()
            if row is None:
                return None
            v = row[0]
            return str(v) if v is not None else None

    async def set_setting(self, key: str, value: str) -> None:
        self._ensure_parent()
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            await conn.commit()


def stable_conversation_id(channel: str, channel_conversation_id: str, repo_path: str) -> str:
    import hashlib

    key = f"{channel}:{channel_conversation_id}:{repo_path}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]
