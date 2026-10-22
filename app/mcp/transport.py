"""Build an isolated stdio environment; no Telegram or LLM credentials enter the child."""

import sys
from pathlib import Path

from mcp.client.stdio import StdioServerParameters

from app.config import Settings


def stdio_transport(settings: Settings, secret: bytes) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp"],
        cwd=Path(__file__).resolve().parents[2],
        env={
            "POSTGRES_HOST": settings.postgres_host,
            "POSTGRES_PORT": str(settings.postgres_port),
            "POSTGRES_DB": settings.postgres_db,
            "POSTGRES_USER": settings.postgres_user,
            "POSTGRES_PASSWORD": settings.postgres_password,
            "MCP_SIGNING_KEY": secret.hex(),
            "SCHEDULE_PATH": settings.schedule_path,
            "SCHEDULE_TIMEZONE": settings.schedule_timezone,
        },
    )
