from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncpg

from app.context import trim_history
from app.llm import TEMPERATURES
from app.prompts import MODES

Owner = tuple[int, int]
BUSY_MESSAGE = (
    "Предыдущий запрос ещё выполняется. Дождитесь завершения и повторите команду или сообщение."
)


class DialogueBusy(Exception):
    """Another transaction is already processing this dialogue."""


@dataclass(frozen=True)
class UserSettings:
    mode: str = "study"
    temperature: float = 0.3


class Repository:
    def __init__(self, pool: asyncpg.Pool, connection=None, retention_days=30):
        self.pool = pool
        self.connection = connection
        self.retention_days = retention_days

    @property
    def db(self):
        return self.connection if self.connection is not None else self.pool

    @asynccontextmanager
    async def locked(self, owner: Owner):
        if self.connection is not None:
            yield self
            return
        async with self.pool.acquire() as connection, connection.transaction():
            acquired = await connection.fetchval(
                "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0))",
                f"{owner[0]}:{owner[1]}",
            )
            if not acquired:
                raise DialogueBusy
            yield Repository(self.pool, connection, self.retention_days)

    async def get_settings(self, owner: Owner) -> UserSettings:
        await self.db.execute(
            "INSERT INTO user_settings (chat_id, user_id) VALUES ($1, $2) "
            "ON CONFLICT (chat_id, user_id) DO NOTHING",
            *owner,
        )
        row = await self.db.fetchrow(
            "SELECT mode, temperature FROM user_settings WHERE chat_id=$1 AND user_id=$2",
            *owner,
        )
        return UserSettings(row["mode"], float(row["temperature"]))

    async def add_message(self, owner: Owner, role: str, content: str, mode: str) -> None:
        if role not in {"user", "assistant"} or not content.strip() or mode not in MODES:
            raise ValueError("Invalid message.")
        await self.db.execute(
            "INSERT INTO messages (chat_id, user_id, role, content, mode) "
            "VALUES ($1, $2, $3, $4, $5)",
            *owner,
            role,
            content,
            mode,
        )

    async def get_history(
        self, owner: Owner, mode: str, limit_messages: int, limit_chars: int
    ) -> list[dict[str, str]]:
        rows = await self.db.fetch(
            "SELECT role, content FROM messages WHERE chat_id=$1 AND user_id=$2 "
            "AND mode=$3 AND created_at >= now() - $5::integer * interval '1 day' "
            "ORDER BY id DESC LIMIT $4",
            *owner,
            mode,
            max(0, limit_messages) + 2,
            self.retention_days,
        )
        history = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
        return trim_history(history, limit_messages, limit_chars)

    async def clear_history(self, owner: Owner) -> None:
        await self.db.execute("DELETE FROM messages WHERE chat_id=$1 AND user_id=$2", *owner)

    async def set_mode(self, owner: Owner, mode: str) -> None:
        if mode not in MODES:
            raise ValueError("Invalid mode.")
        async with self.locked(owner) as repository:
            await repository.get_settings(owner)
            await repository.db.execute(
                "UPDATE user_settings SET mode=$3 WHERE chat_id=$1 AND user_id=$2", *owner, mode
            )
            await repository.clear_history(owner)

    async def set_temperature(self, owner: Owner, temperature: float) -> None:
        if type(temperature) not in {int, float} or temperature not in TEMPERATURES:
            raise ValueError("Invalid temperature.")
        async with self.locked(owner) as repository:
            await repository.get_settings(owner)
            await repository.db.execute(
                "UPDATE user_settings SET temperature=$3 WHERE chat_id=$1 AND user_id=$2",
                *owner,
                temperature,
            )
