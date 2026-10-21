from datetime import UTC, datetime, timedelta

import pytest

from app.repository import Repository, UserSettings
from tests.fakes import MemoryPool


async def seed(repository, owner):
    await repository.get_settings(owner)
    await repository.add_message(owner, "user", "question", "study")
    await repository.add_message(owner, "assistant", "answer", "study")


async def test_users_and_chats_are_isolated_and_repository_can_be_recreated():
    pool = MemoryPool()
    repository = Repository(pool)
    for owner in [(1, 1), (2, 2), (3, 1)]:
        await seed(repository, owner)
    await repository.clear_history((1, 1))
    recreated = Repository(pool)
    assert await recreated.get_history((1, 1), "study", 40, 8000) == []
    for owner in [(2, 2), (3, 1)]:
        assert await recreated.get_history(owner, "study", 40, 8000) == [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]


async def test_mode_change_clears_only_owner_history_and_preserves_temperature():
    pool = MemoryPool()
    repo = Repository(pool)
    await seed(repo, (1, 1))
    await seed(repo, (2, 2))
    await repo.set_temperature((1, 1), 0.7)
    await repo.set_mode((1, 1), "music")
    assert await repo.get_settings((1, 1)) == UserSettings("music", 0.7)
    assert await repo.get_settings((2, 2)) == UserSettings()
    assert await repo.get_history((1, 1), "study", 40, 8000) == []
    assert len(await repo.get_history((2, 2), "study", 40, 8000)) == 2


async def test_history_limits_apply_and_sql_values_are_bound():
    pool = MemoryPool()
    repo = Repository(pool)
    await seed(repo, (1, 1))
    content = "user-input'); DROP TABLE messages; --"
    await repo.add_message((1, 1), "user", content, "study")
    await repo.add_message((1, 1), "assistant", "ok", "study")
    assert await repo.get_history((1, 1), "study", 2, 8000) == [
        {"role": "user", "content": content},
        {"role": "assistant", "content": "ok"},
    ]
    assert await repo.get_history((1, 1), "study", 40, 1) == []
    assert all(content not in query for query, _ in pool.calls)


async def test_invalid_settings_are_rejected_before_sql():
    pool = MemoryPool()
    repo = Repository(pool)
    with pytest.raises(ValueError):
        await repo.set_temperature((1, 1), 0.4)
    with pytest.raises(ValueError):
        await repo.set_mode((1, 1), "unknown")
    assert pool.calls == []


async def test_expired_messages_never_return_to_context_before_cleanup():
    pool = MemoryPool()
    repository = Repository(pool, retention_days=30)
    await seed(repository, (1, 1))
    for row in pool.messages:
        row["created_at"] = datetime.now(UTC) - timedelta(days=31)
    await seed(repository, (1, 1))
    history = await repository.get_history((1, 1), "study", 40, 8000)
    assert len(history) == 2
    assert len(pool.messages) == 4
