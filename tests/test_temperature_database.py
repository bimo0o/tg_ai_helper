import asyncio
import os
from unittest.mock import AsyncMock

import pytest

from app.db import create_pool
from app.llm import Generation
from app.operations import OperationsRepository
from app.retention import purge
from app.schema import migrate
from app.temperature import TemperatureRepository, TemperatureService
from tests.test_integration import database as database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="Requires Docker opt-in"),
]


async def test_real_postgres_claims_isolation_restart_recovery_and_retention(database):
    settings, _, _, _ = database
    pool = await create_pool(settings)
    try:
        await migrate(pool)
        operations = OperationsRepository(pool)
        await operations.assign_owner(10)
        llm = AsyncMock()
        llm.generate.return_value = Generation(True, "answer")
        repository = TemperatureRepository(pool)
        test = TemperatureService(repository, operations, llm, settings)
        key = await test.prepare(10)
        assert await repository.get(20, key) is None
        assert not await repository.claim(20, key, 60)
        results = await asyncio.gather(repository.claim(10, key, 60), repository.claim(10, key, 60))
        assert results.count(True) == 1
        other = await test.prepare(10)
        assert not await repository.claim(10, other, 60)
        await repository.save(
            10,
            key,
            {
                **(await repository.get(10, key))["data"],
                "rows": [{"answer": "saved before restart"}],
            },
        )
        await pool.execute(
            "UPDATE temperature_experiments SET lease_until=now()-interval '1 second' WHERE id=$1",
            key,
        )
        # New repository instance reads persisted results and detects a dead worker.
        restored = TemperatureRepository(pool)
        interrupted = await restored.get(10, key)
        assert interrupted["status"] == "interrupted"
        assert interrupted["data"]["rows"][0]["answer"] == "saved before restart"
        assert not await restored.claim(10, key, 60)
        assert await test.run(10, other, AsyncMock())
        assert llm.generate.await_count == 12
        assert len((await restored.get(10, other))["data"]["rows"]) == 12
        assert (
            await pool.fetchval("SELECT count(*) FROM request_events WHERE mode='temperature'")
            == 12
        )
        expired = await test.prepare(10)
        await pool.execute(
            "UPDATE temperature_experiments SET created_at=now()-interval '16 minutes' WHERE id=$1",
            expired,
        )
        assert not await restored.claim(10, expired, 60)
        await pool.execute(
            "UPDATE temperature_experiments SET expires_at=now()-interval '1 second' WHERE id=$1",
            key,
        )
        assert await restored.get(10, key) is None
        await purge(pool, settings)
        assert not await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM temperature_experiments WHERE id=$1)", key
        )
    finally:
        await pool.close()
