import asyncio
import logging

from app.config import Settings

logger = logging.getLogger(__name__)


async def purge(pool, settings: Settings) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "DELETE FROM messages WHERE created_at < now() - $1::integer * interval '1 day'",
            settings.history_retention_days,
        )
        await connection.execute(
            "DELETE FROM request_events WHERE created_at < now() - $1::integer * interval '1 day'",
            settings.statistics_retention_days,
        )
        await connection.execute(
            "DELETE FROM music_profiles WHERE last_used_at "
            "< now() - $1::integer * interval '1 day'",
            settings.music_profile_retention_days,
        )
        await connection.execute("DELETE FROM temperature_experiments WHERE expires_at < now()")
        await connection.execute(
            "DELETE FROM personal_profiles WHERE last_used_at < now()-interval '180 days'"
        )
        await connection.execute(
            "UPDATE reminder_actions SET status='expired' "
            "WHERE status IN ('pending','confirmed') AND expires_at <= now()"
        )
        await connection.execute(
            "DELETE FROM reminders WHERE finished_at < now()-interval '30 days' "
            "AND status IN ('sent','unconfirmed','blocked','missed','cancelled')"
        )
        await connection.execute(
            "DELETE FROM reminder_actions a WHERE created_at < now()-interval '30 days' "
            "AND status IN ('executed','cancelled','expired') "
            "AND NOT EXISTS (SELECT 1 FROM reminders r WHERE r.action_id=a.id)"
        )
        await connection.execute(
            "DELETE FROM tool_audit WHERE created_at < now()-interval '90 days'"
        )
        await connection.execute(
            "DELETE FROM agent_decisions WHERE created_at < now()-interval '90 days'"
        )


async def maintenance(pool, settings: Settings) -> None:
    while True:
        try:
            await purge(pool, settings)
        except Exception:
            logger.warning("Retention cleanup failed; no user data logged.")
        await asyncio.sleep(3600)
