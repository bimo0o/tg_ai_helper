"""Versioned, transactional schema installation using the application's pool."""

import asyncpg

SCHEMA_VERSION = 11
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

ADMIN_DDL = """
CREATE TABLE administrators (
    user_id BIGINT PRIMARY KEY CHECK (user_id > 0),
    singleton BOOLEAN NOT NULL DEFAULT TRUE UNIQUE CHECK (singleton),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE application_settings (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    model TEXT NOT NULL
);
CREATE TABLE request_events (
    request_id UUID PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started', 'success', 'error')),
    duration_ms BIGINT CHECK (duration_ms >= 0),
    prompt_tokens BIGINT CHECK (prompt_tokens >= 0),
    completion_tokens BIGINT CHECK (completion_tokens >= 0),
    total_tokens BIGINT CHECK (total_tokens >= 0),
    cost_usd NUMERIC(20, 10) CHECK (cost_usd >= 0),
    error_kind TEXT
);
CREATE INDEX request_events_created ON request_events (created_at);
CREATE INDEX request_events_user_created ON request_events (user_id, created_at);
"""
POLICY_DDL = """
ALTER TABLE request_events ADD COLUMN reserved_usd NUMERIC(20, 10)
    NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0);
UPDATE request_events SET reserved_usd=COALESCE(cost_usd, 0.05);
CREATE TABLE music_profiles (
    user_id BIGINT PRIMARY KEY,
    data JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(data) = 'object'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX music_profiles_last_used ON music_profiles (last_used_at);
"""
MODEL_DDL = """
UPDATE application_settings SET model='deepseek/deepseek-v3.2'
WHERE singleton=TRUE AND model IN (
    'qwen/qwen3-235b-a22b-2507', 'deepseek/deepseek-chat-v3-0324'
);
"""
FLASH_DDL = """
UPDATE application_settings SET model='deepseek/deepseek-v4.1-flash'
WHERE singleton=TRUE AND model='deepseek/deepseek-v3.2';
"""
GENERAL_MODES_DDL = """
ALTER TABLE user_settings DROP CONSTRAINT user_settings_mode_check;
ALTER TABLE user_settings ADD CONSTRAINT user_settings_mode_check
    CHECK (mode IN ('study', 'translate', 'music', 'chat', 'web'));
ALTER TABLE messages DROP CONSTRAINT messages_mode_check;
ALTER TABLE messages ADD CONSTRAINT messages_mode_check
    CHECK (mode IN ('study', 'translate', 'music', 'chat', 'web'));
"""
NO_LIMITS_DDL = """
UPDATE request_events SET reserved_usd=0 WHERE reserved_usd <> 0;
"""
TEMPERATURE_DDL = """
CREATE TABLE temperature_experiments (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '30 days',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'running', 'completed', 'cancelled', 'interrupted')),
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    lease_until TIMESTAMPTZ,
    data JSONB NOT NULL CHECK (jsonb_typeof(data) = 'object')
);
CREATE UNIQUE INDEX temperature_one_running ON temperature_experiments (user_id)
    WHERE status='running';
CREATE INDEX temperature_owner_created ON temperature_experiments (user_id, created_at DESC);
"""
PERSONAL_PROFILE_DDL = """
CREATE TABLE personal_profiles (
    user_id BIGINT PRIMARY KEY,
    summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 700),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX personal_profiles_last_used ON personal_profiles (last_used_at);
"""
REMINDERS_DDL = """
CREATE TABLE reminder_timezones (
    user_id BIGINT PRIMARY KEY,
    timezone TEXT NOT NULL CHECK (length(timezone) BETWEEN 1 AND 100)
);
CREATE TABLE reminder_actions (
    id UUID PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    source_key TEXT NOT NULL CHECK (length(source_key) BETWEEN 1 AND 100),
    text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 500),
    remind_at TIMESTAMPTZ NOT NULL,
    timezone TEXT NOT NULL CHECK (length(timezone) BETWEEN 1 AND 100),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'executed', 'cancelled', 'expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '5 minutes',
    UNIQUE (chat_id, user_id, source_key)
);
CREATE TABLE reminders (
    id UUID PRIMARY KEY,
    action_id UUID NOT NULL UNIQUE REFERENCES reminder_actions (id),
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 500),
    remind_at TIMESTAMPTZ NOT NULL,
    timezone TEXT NOT NULL CHECK (length(timezone) BETWEEN 1 AND 100),
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'sending', 'sent', 'unconfirmed',
                          'blocked', 'missed', 'cancelled')),
    claim_token UUID,
    lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    CHECK ((status='sending') = (claim_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX reminders_due ON reminders (remind_at) WHERE status='scheduled';
CREATE INDEX reminders_owner ON reminders (chat_id, user_id, remind_at);
CREATE TABLE tool_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    tool TEXT NOT NULL CHECK (tool IN ('get_weather', 'get_schedule', 'add_reminder')),
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'error', 'confirmation_required')),
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX tool_audit_owner ON tool_audit (chat_id, user_id, created_at DESC);
"""
DECISIONS_DDL = """
CREATE TABLE agent_decisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    source_key TEXT NOT NULL CHECK (length(source_key) BETWEEN 1 AND 100),
    action TEXT NOT NULL CHECK (action IN ('answer','clarification','tool','limit','error')),
    tool TEXT CHECK (tool IN ('get_weather','get_schedule','add_reminder')),
    arguments JSONB NOT NULL CHECK (jsonb_typeof(arguments)='object'),
    status TEXT NOT NULL
        CHECK (status IN ('not_called','awaiting_confirmation','success','error','limit')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chat_id,user_id,source_key)
);
CREATE INDEX agent_decisions_owner ON agent_decisions (chat_id,user_id,created_at DESC);
"""
MIGRATIONS = {
    1: DDL,
    2: ADMIN_DDL,
    3: POLICY_DDL,
    4: MODEL_DDL,
    5: FLASH_DDL,
    6: GENERAL_MODES_DDL,
    7: NO_LIMITS_DDL,
    8: TEMPERATURE_DDL,
    9: PERSONAL_PROFILE_DDL,
    10: REMINDERS_DDL,
    11: DECISIONS_DDL,
}


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
        for target, ddl in MIGRATIONS.items():
            if target <= (version or 0):
                continue
            await connection.execute(ddl)
            await connection.execute("INSERT INTO schema_migrations (version) VALUES ($1)", target)
