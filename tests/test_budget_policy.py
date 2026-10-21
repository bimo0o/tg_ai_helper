from datetime import UTC, datetime
from decimal import Decimal

from app.operations import OperationsRepository
from tests.fakes import MemoryPool


async def test_many_requests_are_recorded_without_local_budget_or_rate_checks():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    for _ in range(60):
        await repo.start_request((1, 1), "model")
    assert len(pool.events) == 60
    assert all(row["reserved_usd"] == 0 for row in pool.events.values())
    assert all("pg_advisory" not in query for query, _ in pool.calls)


async def test_unknown_cost_is_excluded_from_spending_but_remains_unknown():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    event = await repo.start_request((1, 1), "model")
    pool.events[event]["reserved_usd"] = Decimal("0.05")
    await repo.finish_request(event, "study", False, 30000, {}, None, "timeout")
    assert pool.events[event]["reserved_usd"] == 0
    assert pool.events[event]["cost_usd"] is None
    summary = await repo.summary(datetime(2000, 1, 1, tzinfo=UTC))
    assert summary["priced"] == 0 and summary["cost_usd"] is None


async def test_reported_six_request_case_no_longer_blocks_seventh_request():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    for cost in [None, None, None, Decimal("0"), Decimal("0.000956"), Decimal("0.001")]:
        event = await repo.start_request((1, 1), "model")
        await repo.finish_request(event, "music", cost is not None, 100, {}, cost, None)
    summary = await repo.summary(datetime(2000, 1, 1, tzinfo=UTC))
    assert summary["priced"] == 3 and summary["cost_usd"] == Decimal("0.001956")
    await repo.start_request((1, 1), "model")
    assert len(pool.events) == 7
