"""Versioned, transactional schema installation using the application's pool."""

import asyncpg

SCHEMA_VERSION = 1
DDL = """
CREATE TABLE IF NOT EXISTS user_settings (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'study' CHECK (mode IN ('study', 'translate', 'music')),
    temperature NUMERIC(2, 1) NOT NULL DEFAULT 0.3
        CHECK (temperature IN (0.0, 0.3, 0.7, 1.0)),
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(content) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mode TEXT NOT NULL CHECK (mode IN ('study', 'translate', 'music')),
    FOREIGN KEY (chat_id, user_id) REFERENCES user_settings (chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS messages_owner_order ON messages (chat_id, user_id, id);
"""


async def migrate(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock($1)", 745312001)
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        version = await connection.fetchval("SELECT max(version) FROM schema_migrations")
        if version is not None and version > SCHEMA_VERSION:
            raise RuntimeError("Database schema is newer than this application.")
        if version is None:
            await connection.execute(DDL)
            await connection.execute(
                "INSERT INTO schema_migrations (version) VALUES ($1)", SCHEMA_VERSION
            )
