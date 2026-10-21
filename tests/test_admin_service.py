from decimal import Decimal

from app.admin import AdminService
from app.operations import MODEL_OPTIONS, OperationsRepository
from tests.fakes import MemoryPool
from tests.test_billing import client


async def test_unauthorized_access_never_fetches_budget_or_statistics():
    pool = MemoryPool()
    billing, http = client({})
    service = AdminService(OperationsRepository(pool), billing, "fallback")
    assert await service.overview(10) is None
    http.get.assert_not_called()
    assert len(pool.calls) == 1


async def test_dashboard_reports_pagination_cost_coverage_and_db_model():
    pool = MemoryPool()
    operations = OperationsRepository(pool)
    await operations.assign_owner(10)
    await operations.set_model(10, "qwen")
    for user_id in range(1, 13):
        event = await operations.start_request((user_id, user_id), "model")
        await operations.finish_request(event, "study", True, 100, {}, Decimal("0.01"), None)
    billing, _ = client({"data": {"usage": 1, "limit": None, "limit_remaining": None}})
    service = AdminService(operations, billing, "fallback")
    first = await service.overview(10, "all")
    assert first.has_next and MODEL_OPTIONS["qwen"] in first.text
    assert "Запросов к LLM: 12; пользователей: 12" in first.text
    assert "Стоимость известна для 12 из 12" in first.text
    assert "Остаток лимита ключа: лимит не задан" in first.text
    assert "Баланс аккаунта: недоступно" in first.text
    assert "Контроль расходов · сутки UTC:" in first.text
    assert "$2.000000" in first.text and "$0.200000" in first.text
    assert "ID 11:" not in first.text
    second = await service.overview(10, "all", 10)
    assert not second.has_next and "ID 11:" in second.text


async def test_old_statistics_are_hidden_even_before_physical_cleanup():
    from datetime import UTC, datetime, timedelta

    pool = MemoryPool()
    operations = OperationsRepository(pool)
    await operations.assign_owner(10)
    event = await operations.start_request((1, 1), "model")
    pool.events[event]["created_at"] = datetime.now(UTC) - timedelta(days=91)
    billing, _ = client({})
    view = await AdminService(operations, billing, "model").overview(10, "all")
    assert "Запросов к LLM: 0; пользователей: 0" in view.text
