from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

from app.assistant import ERROR_MESSAGE, Assistant
from app.operations import MODEL_OPTIONS, OperationsRepository
from app.repository import BUSY_MESSAGE
from tests.test_assistant import assistant
from tests.test_llm import response


async def test_db_selected_model_is_used_by_next_request_and_after_service_restart():
    data = response()
    data["usage"]["cost"] = 0.0005
    service, pool, http = assistant(data)
    operations = OperationsRepository(pool)
    await operations.assign_owner(10)
    await operations.set_model(10, "qwen")
    service.operations = operations
    assert (await service.respond((1, 1), "private input")).ok
    assert http.post.call_args.kwargs["json"]["model"] == MODEL_OPTIONS["qwen"]
    await operations.set_model(10, "deepseek")
    restarted = Assistant(
        service.repository, service.llm, service.settings, OperationsRepository(pool)
    )
    assert (await restarted.respond((1, 1), "second")).ok
    assert http.post.call_args.kwargs["json"]["model"] == MODEL_OPTIONS["deepseek"]
    assert len(pool.events) == 2
    assert all(row["status"] == "success" for row in pool.events.values())
    assert all("private input" not in str(row) for row in pool.events.values())
    summary = await operations.summary(datetime(2000, 1, 1, tzinfo=UTC))
    assert summary["cost_usd"] == Decimal("0.001")


async def test_timeout_count_survives_reset_without_inventing_cost():
    service, pool, _ = assistant(error=TimeoutError())
    service.operations = OperationsRepository(pool)
    assert not (await service.respond((1, 1), "question")).ok
    await service.repository.clear_history((1, 1))
    event = next(iter(pool.events.values()))
    assert event["status"] == "error" and event["error_kind"] == "timeout"
    assert event["cost_usd"] is None


async def test_invalid_input_does_not_create_api_event():
    service, pool, http = assistant()
    service.operations = OperationsRepository(pool)
    await service.respond((1, 1), "x" * 8001)
    assert pool.events == {}
    http.post.assert_not_called()


async def test_database_failure_after_generation_still_records_known_cost(monkeypatch):
    data = response()
    data["usage"]["cost"] = 0.001
    service, pool, _ = assistant(data)
    service.operations = OperationsRepository(pool)
    from app.repository import Repository

    original = Repository.add_message

    async def add(self, owner, role, content, mode):
        if role == "assistant":
            raise ConnectionError("example-error")
        await original(self, owner, role, content, mode)

    monkeypatch.setattr(Repository, "add_message", add)
    assert not (await service.respond((1, 1), "question")).ok
    event = next(iter(pool.events.values()))
    assert event["status"] == "error" and event["cost_usd"] == Decimal("0.001")


async def test_commit_failure_after_generation_is_not_reported_as_success(monkeypatch):
    data = response()
    data["usage"]["cost"] = 0.001
    service, pool, _ = assistant(data)
    service.operations = OperationsRepository(pool)
    transactions = 0

    @asynccontextmanager
    async def transaction():
        nonlocal transactions
        transactions += 1
        current = transactions
        yield pool
        if current == 2:
            raise ConnectionError("example commit failure")

    monkeypatch.setattr(pool, "transaction", transaction)
    reply = await service.respond((1, 1), "question")
    assert not reply.ok
    assert reply.text == ERROR_MESSAGE
    event = next(iter(pool.events.values()))
    assert event["status"] == "error"
    assert event["cost_usd"] == Decimal("0.001")


async def test_busy_dialogue_does_not_call_api_or_keep_money_reserved(monkeypatch):
    service, pool, http = assistant()
    service.operations = OperationsRepository(pool)
    original = pool.fetchval

    async def fetchval(query, *args):
        if query.startswith("SELECT pg_try_advisory_xact_lock"):
            return False
        return await original(query, *args)

    monkeypatch.setattr(pool, "fetchval", AsyncMock(side_effect=fetchval))
    reply = await service.respond((1, 1), "question")
    assert not reply.ok and reply.text == BUSY_MESSAGE
    http.post.assert_not_called()
    assert pool.messages == []
    event = next(iter(pool.events.values()))
    assert event["error_kind"] == "busy" and event["status"] == "error"
    assert event["cost_usd"] == event["reserved_usd"] == Decimal("0")
