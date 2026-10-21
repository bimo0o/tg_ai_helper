from dataclasses import replace
from decimal import Decimal

import pytest

from app.config import Settings
from app.operations import LimitExceeded, OperationsRepository
from tests.fakes import MemoryPool


def limits(**changes):
    return replace(Settings(bot_token="unused", postgres_password="unused"), **changes)


async def test_reservation_blocks_user_before_api_budget_is_exhausted():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    policy = limits(user_daily_budget_usd=Decimal("0.05"))
    await repo.start_request((1, 1), "model", policy)
    with pytest.raises(LimitExceeded, match="user_budget"):
        await repo.start_request((1, 1), "model", policy)
    assert len(pool.events) == 1
    await repo.start_request((2, 2), "model", policy)
    assert len(pool.events) == 2


async def test_global_reservation_and_release_of_known_cost():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    policy = limits(
        daily_budget_usd=Decimal("0.11"),
        user_daily_budget_usd=Decimal("0.11"),
        user_requests_per_minute=10,
    )
    first = await repo.start_request((1, 1), "model", policy)
    await repo.start_request((2, 2), "model", policy)
    with pytest.raises(LimitExceeded, match="global_budget"):
        await repo.start_request((3, 3), "model", policy)
    await repo.finish_request(first, "study", True, 10, {}, Decimal("0.01"), None)
    await repo.start_request((3, 3), "model", policy)
    assert sum(row["reserved_usd"] for row in pool.events.values()) == Decimal("0.11")


async def test_unknown_cost_keeps_reserve_and_rate_limits_are_enforced():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    policy = limits(user_requests_per_minute=1)
    event = await repo.start_request((1, 1), "model", policy)
    await repo.finish_request(event, "study", False, 30000, {}, None, "timeout")
    assert pool.events[event]["reserved_usd"] == Decimal("0.05")
    with pytest.raises(LimitExceeded, match="rate"):
        await repo.start_request((1, 1), "model", policy)
    assert any(
        query == "SELECT pg_advisory_xact_lock($1)" and args == (745312002,)
        for query, args in pool.calls
    )


async def test_daily_request_quota_is_independent_of_low_cost():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    policy = limits(user_requests_per_day=1, user_requests_per_minute=10)
    event = await repo.start_request((1, 1), "model", policy)
    await repo.finish_request(event, "study", True, 10, {}, Decimal("0"), None)
    with pytest.raises(LimitExceeded, match="daily_requests"):
        await repo.start_request((1, 1), "model", policy)
