#!/usr/bin/env python3
"""
Hard-delete all agent session rows (and session_messages, session_participants) from SQLite.

Use when the server is stopped, or when no ACP processes are tied to old session ids.
Default DB: data/control_plane.db next to the repo root.

  python scripts/purge_all_sessions_db.py
  python scripts/purge_all_sessions_db.py path/to/custom.db
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Repo root = parent of scripts/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from control_plane.db import Database  # noqa: E402


async def main() -> None:
    db_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _ROOT / "data" / "control_plane.db"
    if not db_path.is_file():
        print(f"Database file not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    db = Database(db_path)
    n = await db.delete_all_sessions()
    print(f"Removed {n} session row(s) from {db_path}")


if __name__ == "__main__":
    asyncio.run(main())
