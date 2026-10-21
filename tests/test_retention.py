import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.music_profile import MusicProfiles
from app.operations import OperationsRepository
from app.repository import Repository
from app.retention import maintenance, purge
from tests.fakes import MemoryPool


async def test_cleanup_applies_distinct_ages_and_preserves_recent_data_and_settings():
    pool = MemoryPool()
    repo = Repository(pool)
    await repo.get_settings((1, 1))
    await repo.add_message((1, 1), "user", "old", "study")
    pool.messages[0]["created_at"] = datetime.now(UTC) - timedelta(days=31)
    await repo.add_message((1, 1), "user", "new", "study")
    operations = OperationsRepository(pool)
    old = await operations.start_request((1, 1), "model")
    pool.events[old]["created_at"] = datetime.now(UTC) - timedelta(days=91)
    current = await operations.start_request((2, 2), "model")
    profiles = MusicProfiles(pool)
    await profiles.set_field(1, "daw", "old")
    pool.profiles[1]["last_used_at"] = datetime.now(UTC) - timedelta(days=181)
    await profiles.set_field(2, "daw", "new")
    await purge(pool, Settings(bot_token="unused", postgres_password="unused"))
    assert [row["content"] for row in pool.messages] == ["new"]
    assert set(pool.events) == {current} and set(pool.profiles) == {2}
    assert (1, 1) in pool.settings


async def test_maintenance_runs_immediately_and_cancels_cleanly(monkeypatch):
    from app import retention

    called = asyncio.Event()

    async def cleanup(pool, settings):
        called.set()

    monkeypatch.setattr(retention, "purge", cleanup)
    task = asyncio.create_task(
        maintenance(None, Settings(bot_token="unused", postgres_password="unused"))
    )
    await asyncio.wait_for(called.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
