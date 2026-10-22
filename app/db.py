from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from app.config import Settings


@dataclass(frozen=True)
class DatabaseSettings:
    """Minimal child-process settings; importing them never imports Telegram."""

    postgres_password: str = field(repr=False)
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "bot"
    postgres_user: str = "bot"


async def create_pool(settings: Settings | DatabaseSettings) -> asyncpg.Pool:
    # Небольшого пула достаточно для учебной ВМ. Таблицы добавляют студенты.
    pool = await asyncpg.create_pool(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        min_size=1,
        max_size=5,
        timeout=5,
        command_timeout=5,
    )
    try:
        await pool.fetchval("SELECT 1", timeout=3)
    except BaseException:
        await pool.close()
        raise
    return pool
