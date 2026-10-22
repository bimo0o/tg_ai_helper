"""Durable reminder state machine. All owner checks happen in SQL, not in the model."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.reminder_time import ReminderInputError, reminder_instant, timezone

Owner = tuple[int, int]


class ReminderUnavailable(ValueError):
    """Action is missing, expired, belongs to another owner or has changed state."""


class Reminders:
    def __init__(self, pool, *, clock: Callable[[], datetime] | None = None):
        self.pool = pool
        self.clock = clock or (lambda: datetime.now(UTC))

    async def set_timezone(self, user_id: int, name: str) -> None:
        timezone(name)
        await self.pool.execute(
            "INSERT INTO reminder_timezones (user_id, timezone) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET timezone=EXCLUDED.timezone",
            user_id,
            name,
        )

    async def get_timezone(self, user_id: int) -> str | None:
        return await self.pool.fetchval(
            "SELECT timezone FROM reminder_timezones WHERE user_id=$1", user_id
        )

    async def propose(
        self, owner: Owner, source_key: str, text: str, remind_at: str, zone: str
    ) -> dict:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            raise ReminderInputError("Текст напоминания должен содержать от 1 до 500 символов.")
        if not isinstance(source_key, str) or not 1 <= len(source_key) <= 100:
            raise ValueError("Invalid source identity")
        now = self.clock()
        instant = reminder_instant(remind_at, zone, now)
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "INSERT INTO reminder_actions "
                "(id, chat_id, user_id, source_key, text, remind_at, timezone, "
                "created_at, expires_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) "
                "ON CONFLICT (chat_id, user_id, source_key) DO NOTHING",
                uuid4(),
                *owner,
                source_key,
                text.strip(),
                instant,
                zone,
                now,
                now + timedelta(minutes=5),
            )
            row = await connection.fetchrow(
                "SELECT * FROM reminder_actions WHERE chat_id=$1 AND user_id=$2 AND source_key=$3",
                *owner,
                source_key,
            )
            # Duplicate updates can only reuse the original immutable proposal.
            return dict(row)

    async def confirm(self, owner: Owner, action_id: UUID) -> dict:
        """Persist user approval separately; MCP execution can safely retry it later."""
        async with self.pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                "SELECT * FROM reminder_actions WHERE id=$1 AND chat_id=$2 AND user_id=$3 "
                "FOR UPDATE",
                action_id,
                *owner,
            )
            if not row or row["status"] not in {"pending", "confirmed", "executed"}:
                raise ReminderUnavailable
            if row["status"] != "executed" and (
                row["expires_at"] <= self.clock() or row["remind_at"] <= self.clock()
            ):
                raise ReminderUnavailable
            if row["status"] == "pending":
                await connection.execute(
                    "UPDATE reminder_actions SET status='confirmed' WHERE id=$1", action_id
                )
            return {
                **dict(row),
                "status": "confirmed" if row["status"] == "pending" else row["status"],
            }

    async def execute(
        self, owner: Owner, action_id: UUID, text: str, remind_at: str
    ) -> tuple[dict, bool]:
        """Called by trusted MCP adapter after signature validation; DB is the authority."""
        async with self.pool.acquire() as connection, connection.transaction():
            action = await connection.fetchrow(
                "SELECT * FROM reminder_actions WHERE id=$1 AND chat_id=$2 AND user_id=$3 "
                "FOR UPDATE",
                action_id,
                *owner,
            )
            if not action or action["status"] not in {"confirmed", "executed"}:
                raise ReminderUnavailable
            try:
                supplied = datetime.fromisoformat(remind_at)
            except (TypeError, ValueError):
                raise ReminderUnavailable from None
            if supplied.tzinfo is None or supplied != action["remind_at"] or text != action["text"]:
                raise ReminderUnavailable
            if action["status"] == "executed":
                existing = await connection.fetchrow(
                    "SELECT * FROM reminders WHERE action_id=$1", action_id
                )
                if not existing:
                    raise ReminderUnavailable
                return dict(existing), False
            if action["expires_at"] <= self.clock() or action["remind_at"] <= self.clock():
                raise ReminderUnavailable
            row = await connection.fetchrow(
                "INSERT INTO reminders "
                "(id, action_id, chat_id, user_id, text, remind_at, timezone) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *",
                uuid4(),
                action_id,
                *owner,
                action["text"],
                action["remind_at"],
                action["timezone"],
            )
            await connection.execute(
                "UPDATE reminder_actions SET status='executed' WHERE id=$1", action_id
            )
            return dict(row), True

    async def dismiss(self, owner: Owner, action_id: UUID) -> bool:
        row = await self.pool.fetchrow(
            "UPDATE reminder_actions SET status='cancelled' "
            "WHERE id=$1 AND chat_id=$2 AND user_id=$3 AND status='pending' RETURNING id",
            action_id,
            *owner,
        )
        return row is not None

    async def list(self, owner: Owner) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT * FROM reminders WHERE chat_id=$1 AND user_id=$2 "
            "AND status IN ('scheduled','sending','unconfirmed') ORDER BY remind_at, id LIMIT 50",
            *owner,
        )
        return [dict(row) for row in rows]

    async def cancel(self, owner: Owner, reminder_id: UUID) -> bool:
        row = await self.pool.fetchrow(
            "UPDATE reminders SET status='cancelled', finished_at=$4 "
            "WHERE id=$1 AND chat_id=$2 AND user_id=$3 AND status='scheduled' RETURNING id",
            reminder_id,
            *owner,
            self.clock(),
        )
        return row is not None

    async def claim(self) -> dict | None:
        """One atomic claim across workers; an expired send lease is never retried blindly."""
        now = self.clock()
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "UPDATE reminders SET status='unconfirmed', claim_token=NULL, lease_until=NULL, "
                "finished_at=$1 WHERE status='sending' AND lease_until <= $1",
                now,
            )
            await connection.execute(
                "UPDATE reminders SET status='missed', finished_at=$1 "
                "WHERE status='scheduled' AND remind_at < $1::timestamptz - interval '24 hours'",
                now,
            )
            row = await connection.fetchrow(
                "WITH due AS (SELECT id FROM reminders "
                "WHERE status='scheduled' AND remind_at <= $1 "
                "ORDER BY remind_at, id LIMIT 1 FOR UPDATE SKIP LOCKED) "
                "UPDATE reminders r SET status='sending', claim_token=$2, "
                "lease_until=$1::timestamptz + interval '2 minutes' "
                "FROM due WHERE r.id=due.id RETURNING r.*",
                now,
                uuid4(),
            )
            return dict(row) if row else None

    async def finish(self, reminder_id: UUID, claim_token: UUID, status: str) -> bool:
        if status not in {"sent", "blocked", "unconfirmed"}:
            raise ValueError("Invalid completion status")
        row = await self.pool.fetchrow(
            "UPDATE reminders SET status=$3, claim_token=NULL, lease_until=NULL, finished_at=$4 "
            "WHERE id=$1 AND claim_token=$2 AND status='sending' RETURNING id",
            reminder_id,
            claim_token,
            status,
            self.clock(),
        )
        return row is not None
