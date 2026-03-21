#!/usr/bin/env python3
"""Run the Control Plane (FastAPI + channels)."""

from __future__ import annotations

import uvicorn

from control_plane.config import get_settings


def main() -> None:
    app_config, _env = get_settings()
    uvicorn.run(
        "control_plane.app:create_app",
        factory=True,
        host=app_config.server.host,
        port=app_config.server.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
