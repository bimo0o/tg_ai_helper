from unittest.mock import AsyncMock, Mock

import pytest

from app.schema import (
    ADMIN_DDL,
    DDL,
    DECISIONS_DDL,
    FLASH_DDL,
    GENERAL_MODES_DDL,
    MODEL_DDL,
    NO_LIMITS_DDL,
    PERSONAL_PROFILE_DDL,
    POLICY_DDL,
    REMINDERS_DDL,
    SCHEMA_VERSION,
    TEMPERATURE_DDL,
    migrate,
)


def pool_with_version(version):
    connection = Mock(execute=AsyncMock(), fetchval=AsyncMock(return_value=version))
    transaction = Mock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
    connection.transaction.return_value = transaction
    acquire = Mock(
        __aenter__=AsyncMock(return_value=connection), __aexit__=AsyncMock(return_value=False)
    )
    return Mock(acquire=Mock(return_value=acquire)), connection, transaction


async def test_clean_schema_install_is_transactional_and_versioned():
    pool, connection, transaction = pool_with_version(None)
    await migrate(pool)
    calls = connection.execute.call_args_list
    assert calls[0].args == ("SELECT pg_advisory_xact_lock($1)", 745312001)
    assert calls[2].args == (DDL,)
    assert calls[3].args == ("INSERT INTO schema_migrations (version) VALUES ($1)", 1)
    assert calls[4].args == (ADMIN_DDL,)
    assert calls[5].args == ("INSERT INTO schema_migrations (version) VALUES ($1)", 2)
    assert calls[6].args == (POLICY_DDL,)
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


async def test_second_install_does_not_reapply_ddl():
    pool, connection, _ = pool_with_version(SCHEMA_VERSION)
    await migrate(pool)
    assert all(call.args != (DDL,) for call in connection.execute.call_args_list)


async def test_newer_schema_is_rejected_and_transaction_exits_with_error():
    pool, _, transaction = pool_with_version(SCHEMA_VERSION + 1)
    with pytest.raises(RuntimeError, match="newer"):
        await migrate(pool)
    assert transaction.__aexit__.call_args.args[0] is RuntimeError


async def test_existing_lab1_database_receives_only_admin_migration():
    pool, connection, _ = pool_with_version(1)
    await migrate(pool)
    calls = connection.execute.call_args_list
    assert all(call.args != (DDL,) for call in calls)
    assert calls[2].args == (ADMIN_DDL,)


async def test_admin_database_receives_only_policy_migration():
    pool, connection, _ = pool_with_version(2)
    await migrate(pool)
    assert connection.execute.call_args_list[2].args == (POLICY_DDL,)


async def test_policy_database_upgrades_only_the_selected_model_once():
    pool, connection, _ = pool_with_version(3)
    await migrate(pool)
    assert connection.execute.call_args_list[2].args == (MODEL_DDL,)
    assert connection.execute.call_args_list[3].args == (
        "INSERT INTO schema_migrations (version) VALUES ($1)",
        4,
    )
    assert connection.execute.call_args_list[4].args == (FLASH_DDL,)


async def test_existing_deepseek_database_receives_flash_upgrade():
    pool, connection, _ = pool_with_version(4)
    await migrate(pool)
    assert connection.execute.call_args_list[2].args == (FLASH_DDL,)
    assert connection.execute.call_args_list[3].args == (
        "INSERT INTO schema_migrations (version) VALUES ($1)",
        5,
    )


async def test_flash_database_adds_general_modes_without_deleting_data():
    pool, connection, _ = pool_with_version(5)
    await migrate(pool)
    assert connection.execute.call_args_list[2].args == (GENERAL_MODES_DDL,)
    assert connection.execute.call_args_list[3].args == (
        "INSERT INTO schema_migrations (version) VALUES ($1)",
        6,
    )


async def test_general_database_clears_old_reservations_without_deleting_statistics():
    pool, connection, _ = pool_with_version(6)
    await migrate(pool)
    assert connection.execute.call_args_list[2].args == (NO_LIMITS_DDL,)
    assert connection.execute.call_args_list[3].args == (
        "INSERT INTO schema_migrations (version) VALUES ($1)",
        7,
    )


async def test_existing_database_adds_experiment_storage_without_rewriting_dialogues():
    pool, connection, _ = pool_with_version(7)
    await migrate(pool)
    assert connection.execute.call_args_list[2].args == (TEMPERATURE_DDL,)
    assert connection.execute.call_args_list[3].args == (
        "INSERT INTO schema_migrations (version) VALUES ($1)",
        8,
    )


async def test_existing_experiments_database_adds_personal_profiles_only():
    pool, connection, _ = pool_with_version(8)
    await migrate(pool)
    assert connection.execute.call_args_list[2].args == (PERSONAL_PROFILE_DDL,)
    assert connection.execute.call_args_list[3].args == (
        "INSERT INTO schema_migrations (version) VALUES ($1)",
        9,
    )


async def test_personal_profile_database_adds_reminders_without_touching_existing_tables():
    pool, connection, _ = pool_with_version(9)
    await migrate(pool)
    assert connection.execute.call_args_list[2].args == (REMINDERS_DDL,)
    assert connection.execute.call_args_list[3].args == (
        "INSERT INTO schema_migrations (version) VALUES ($1)",
        10,
    )


async def test_reminders_database_adds_safe_agent_audit_only():
    pool, connection, _ = pool_with_version(10)
    await migrate(pool)
    assert connection.execute.call_args_list[2].args == (DECISIONS_DDL,)
    assert connection.execute.call_args_list[3].args == (
        "INSERT INTO schema_migrations (version) VALUES ($1)",
        11,
    )
