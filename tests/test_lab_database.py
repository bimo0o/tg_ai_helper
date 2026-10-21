import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.db import create_pool
from app.music_profile import MusicProfiles
from app.operations import MODEL_OPTIONS, OperationsRepository
from app.repository import DialogueBusy, Repository, UserSettings
from app.retention import purge
from app.schema import ADMIN_DDL, DDL, SCHEMA_VERSION, migrate
from tests.test_integration import database as database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="Requires Docker opt-in"),
]


async def test_schema_and_dialogue_survive_pool_and_postgres_restart(database):
    settings, docker, wait_until_ready, name = database
    pool = await create_pool(settings)
    try:
        await migrate(pool)
        await migrate(pool)
        assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == SCHEMA_VERSION
        repo = Repository(pool)
        async with repo.locked((1, 1)):
            with pytest.raises(DialogueBusy):
                async with Repository(pool).locked((1, 1)):
                    pytest.fail("A concurrent transaction acquired the same dialogue lock")
            async with Repository(pool).locked((2, 2)):
                pass
        async with repo.locked((1, 1)):
            pass
        await repo.set_temperature((1, 1), 0.7)
        await repo.set_mode((3, 3), "web")
        await repo.add_message((3, 3), "user", "general search", "web")
        await repo.add_message((3, 3), "assistant", "answer with sources", "web")
        for owner in [(1, 1), (2, 2)]:
            await repo.get_settings(owner)
            await repo.add_message(owner, "user", "question", "study")
            await repo.add_message(owner, "assistant", "answer", "study")
    finally:
        await pool.close()
    docker("restart", name)
    wait_until_ready()
    restored_pool = await create_pool(settings)
    try:
        await migrate(restored_pool)
        repo = Repository(restored_pool)
        assert (await repo.get_settings((3, 3))).mode == "web"
        assert len(await repo.get_history((3, 3), "web", 40, 8000)) == 2
        assert await repo.get_settings((1, 1)) == UserSettings("study", 0.7)
        assert len(await repo.get_history((1, 1), "study", 40, 8000)) == 2
        await repo.set_mode((1, 1), "music")
        assert await repo.get_history((1, 1), "study", 40, 8000) == []
        assert len(await repo.get_history((2, 2), "study", 40, 8000)) == 2
        operations = OperationsRepository(restored_pool)
        await operations.assign_owner(10)
        assert await operations.set_model(10, "qwen")
        assert await operations.get_model("fallback") == MODEL_OPTIONS["qwen"]
        event = await operations.start_request((2, 2), MODEL_OPTIONS["qwen"])
        await operations.finish_request(event, "study", True, 10, {}, Decimal("0.01"), None)
        await repo.clear_history((2, 2))
        stats = await operations.summary(datetime(1970, 1, 1, tzinfo=UTC))
        assert stats["requests"] == 1 and stats["cost_usd"] == Decimal("0.01")
        assert stats["cost_usd"] == Decimal("0.01")
        profiles = MusicProfiles(restored_pool)
        await profiles.set_field(2, "daw", "Ableton Live")
        assert await profiles.get(2) == {"daw": "Ableton Live"}
        await restored_pool.execute(
            "UPDATE music_profiles SET last_used_at=now()-interval '181 days' WHERE user_id=$1", 2
        )
        assert await profiles.get(2) == {}
        await purge(restored_pool, settings)
        assert await restored_pool.fetchval("SELECT count(*) FROM music_profiles") == 0
    finally:
        await restored_pool.close()


async def test_budget_migration_accounts_for_older_known_and_unknown_costs(database):
    import uuid

    settings, _, _, _ = database
    pool = await create_pool(settings)
    try:
        await pool.execute(DDL)
        await pool.execute(ADMIN_DDL)
        await pool.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        await pool.execute("INSERT INTO schema_migrations VALUES (1), (2)")
        await pool.execute(
            "INSERT INTO application_settings (model) VALUES ($1)", MODEL_OPTIONS["qwen"]
        )
        for cost in (Decimal("0.03"), None):
            await pool.execute(
                "INSERT INTO request_events "
                "(request_id, chat_id, user_id, model, mode, status, cost_usd) "
                "VALUES ($1, 1, 1, 'model', 'study', 'success', $2)",
                uuid.uuid4(),
                cost,
            )
        await migrate(pool)
        assert await OperationsRepository(pool).get_model("") == MODEL_OPTIONS["deepseek"]
        await pool.execute("UPDATE application_settings SET model=$1", MODEL_OPTIONS["qwen"])
        await migrate(pool)
        assert await OperationsRepository(pool).get_model("") == MODEL_OPTIONS["qwen"]
        assert await pool.fetchval("SELECT sum(reserved_usd) FROM request_events") == Decimal("0")
    finally:
        await pool.close()
