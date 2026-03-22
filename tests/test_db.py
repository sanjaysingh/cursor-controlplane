"""SQLite layer: schema, settings, agent session row."""

from __future__ import annotations

import asyncio

from control_plane.db import Database


def test_init_schema_and_settings_roundtrip(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        await db.init_schema()
        assert db.path.is_file()

        await db.set_setting("default_model", "my-model")
        assert await db.get_setting("default_model") == "my-model"

        await db.set_setting("default_model", "")
        assert await db.get_setting("default_model") == ""

    asyncio.run(run())


def test_insert_agent_session_and_count(tmp_path):
    async def run():
        db = Database(tmp_path / "t.db")
        await db.init_schema()
        await db.insert_agent_session(
            "sess-1",
            "web",
            "web:key",
            "",
            title="T",
            model=None,
        )
        assert await db.count_agent_sessions() == 1
        row = await db.get_agent_session("sess-1")
        assert row is not None
        assert row["channel"] == "web"
        assert row["title"] == "T"

    asyncio.run(run())
