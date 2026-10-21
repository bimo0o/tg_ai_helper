from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.operations import MODEL_OPTIONS, OperationsRepository
from app.repository import Repository
from tests.fakes import MemoryPool


async def test_old_env_default_moves_to_deepseek_but_admin_choice_remains_explicit():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    assert await repo.get_model(MODEL_OPTIONS["qwen"]) == MODEL_OPTIONS["deepseek"]
    assert await repo.get_model("custom-model") == "custom-model"
    await repo.assign_owner(10)
    await repo.set_model(10, "qwen")
    assert await repo.get_model(MODEL_OPTIONS["deepseek"]) == MODEL_OPTIONS["qwen"]


async def test_single_owner_persists_and_unauthorized_model_change_is_rejected():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    assert not await repo.is_admin(10)
    await repo.assign_owner(10)
    await repo.assign_owner(10)
    with pytest.raises(ValueError, match="already"):
        await repo.assign_owner(20)
    assert not await repo.set_model(20, "deepseek")
    assert await repo.set_model(10, "qwen")
    assert await OperationsRepository(pool).get_model("fallback") == MODEL_OPTIONS["qwen"]
    assert await repo.set_model(10, "deepseek")
    assert await repo.get_model("fallback") == MODEL_OPTIONS["deepseek"]


async def test_invalid_model_cannot_reach_sql():
    pool = MemoryPool()
    with pytest.raises(ValueError):
        await OperationsRepository(pool).set_model(10, "untrusted-model")
    assert pool.calls == []


async def test_accounting_survives_reset_and_distinguishes_unknown_cost():
    pool = MemoryPool()
    repo = OperationsRepository(pool)
    first = await repo.start_request((1, 1), "model")
    await repo.finish_request(
        first, "study", True, 120, {"prompt_tokens": 50}, Decimal("0.002"), None
    )
    second = await repo.start_request((2, 2), "model")
    await repo.finish_request(second, "music", False, 30000, {}, None, "timeout")
    await Repository(pool).clear_history((1, 1))
    summary = await repo.summary(datetime(2000, 1, 1, tzinfo=UTC))
    assert summary["requests"] == 2 and summary["users"] == 2
    assert summary["successes"] == 1 and summary["errors"] == 1
    assert summary["priced"] == 1 and summary["cost_usd"] == Decimal("0.002")
    assert len(await repo.user_stats(datetime(2000, 1, 1, tzinfo=UTC))) == 2
    assert len(await repo.user_stats(datetime(2000, 1, 1, tzinfo=UTC), 1)) == 1


@pytest.mark.parametrize("owner", [0, -1, True, 2**63])
async def test_invalid_owner_is_rejected(owner):
    with pytest.raises(ValueError):
        await OperationsRepository(MemoryPool()).assign_owner(owner)
