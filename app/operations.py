import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

MODEL_OPTIONS = {
    "qwen": "qwen/qwen3-235b-a22b-2507",
    "deepseek": "deepseek/deepseek-v3.2",
}
DEFAULT_MODEL = MODEL_OPTIONS["deepseek"]


class LimitExceeded(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class OperationsRepository:
    def __init__(self, pool):
        self.pool = pool

    async def is_admin(self, user_id: int) -> bool:
        return bool(
            await self.pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM administrators WHERE user_id=$1)", user_id
            )
        )

    async def assign_owner(self, user_id: int) -> None:
        if type(user_id) is not int or not 0 < user_id < 2**63:
            raise ValueError("Invalid Telegram user ID.")
        await self.pool.execute(
            "INSERT INTO administrators (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id
        )
        if not await self.is_admin(user_id):
            raise ValueError("An owner has already been assigned.")

    async def get_model(self, fallback: str) -> str:
        return await self.pool.fetchval(
            "SELECT model FROM application_settings WHERE singleton=TRUE"
        ) or (DEFAULT_MODEL if fallback == MODEL_OPTIONS["qwen"] else fallback)

    async def set_model(self, actor_id: int, alias: str) -> bool:
        if alias not in MODEL_OPTIONS:
            raise ValueError("Invalid model selection.")
        result = await self.pool.execute(
            "INSERT INTO application_settings (singleton, model) "
            "SELECT TRUE, $2 WHERE EXISTS (SELECT 1 FROM administrators WHERE user_id=$1) "
            "ON CONFLICT (singleton) DO UPDATE SET model=EXCLUDED.model",
            actor_id,
            MODEL_OPTIONS[alias],
        )
        return result == "INSERT 0 1"

    async def start_request(self, owner: tuple[int, int], model: str, limits=None) -> uuid.UUID:
        if limits is None:
            return await self._insert_request(self.pool, owner, model, Decimal("0"))
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock($1)", 745312002)
            now = datetime.now(UTC)
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            usage = await connection.fetchrow(
                "SELECT COALESCE(sum(reserved_usd) FILTER (WHERE created_at >= $2), 0) "
                "AS global_spend, COALESCE(sum(reserved_usd) FILTER "
                "(WHERE user_id=$1 AND created_at >= $2), 0) AS user_spend, "
                "count(*) FILTER (WHERE user_id=$1 AND created_at >= $2) AS day_count, "
                "count(*) FILTER (WHERE user_id=$1 AND created_at >= $3) AS minute_count "
                "FROM request_events WHERE created_at >= LEAST($2, $3)",
                owner[1],
                day,
                now - timedelta(minutes=1),
            )
            if usage["minute_count"] >= limits.user_requests_per_minute:
                raise LimitExceeded("rate")
            if usage["day_count"] >= limits.user_requests_per_day:
                raise LimitExceeded("daily_requests")
            reserve = limits.request_reserve_usd
            if usage["global_spend"] + reserve > limits.daily_budget_usd:
                raise LimitExceeded("global_budget")
            if usage["user_spend"] + reserve > limits.user_daily_budget_usd:
                raise LimitExceeded("user_budget")
            return await self._insert_request(connection, owner, model, reserve)

    async def _insert_request(self, connection, owner, model, reserve) -> uuid.UUID:
        request_id = uuid.uuid4()
        await connection.execute(
            "INSERT INTO request_events "
            "(request_id, chat_id, user_id, model, mode, status, reserved_usd) "
            "VALUES ($1, $2, $3, $4, 'unknown', 'started', $5)",
            request_id,
            *owner,
            model,
            reserve,
        )
        return request_id

    async def finish_request(
        self,
        request_id: uuid.UUID,
        mode: str,
        ok: bool,
        duration_ms: int,
        usage: dict[str, int],
        cost_usd: Decimal | None,
        error_kind: str | None,
    ) -> None:
        await self.pool.execute(
            "UPDATE request_events SET mode=$2, status=$3, duration_ms=$4, "
            "prompt_tokens=$5, completion_tokens=$6, total_tokens=$7, cost_usd=$8, error_kind=$9, "
            "reserved_usd=COALESCE($8, reserved_usd) "
            "WHERE request_id=$1",
            request_id,
            mode,
            "success" if ok else "error",
            duration_ms,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
            cost_usd,
            error_kind,
        )

    async def summary(self, since: datetime) -> dict:
        row = await self.pool.fetchrow(
            "SELECT count(*) AS requests, count(DISTINCT user_id) AS users, "
            "count(*) FILTER (WHERE status='success') AS successes, "
            "count(*) FILTER (WHERE status='error') AS errors, "
            "count(*) FILTER (WHERE status='started') AS pending, "
            "count(cost_usd) AS priced, sum(cost_usd) AS cost_usd, "
            "sum(reserved_usd) AS reserved_usd, "
            "sum(prompt_tokens) AS prompt_tokens, sum(completion_tokens) AS completion_tokens, "
            "avg(duration_ms) AS average_ms FROM request_events WHERE created_at >= $1",
            since,
        )
        return dict(row)

    async def user_stats(self, since: datetime, offset: int = 0) -> list[dict]:
        if offset < 0:
            raise ValueError("Invalid offset.")
        rows = await self.pool.fetch(
            "SELECT user_id, count(*) AS requests, "
            "count(*) FILTER (WHERE status='error') AS errors, "
            "count(cost_usd) AS priced, sum(cost_usd) AS cost_usd "
            "FROM request_events WHERE created_at >= $1 GROUP BY user_id "
            "ORDER BY requests DESC, user_id LIMIT 11 OFFSET $2",
            since,
            offset,
        )
        return [dict(row) for row in rows]
