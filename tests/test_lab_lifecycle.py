from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app import __main__ as application
from app.config import Settings


async def test_startup_wires_commands_before_text_and_closes_single_pool_and_sessions(monkeypatch):
    pool = Mock(close=AsyncMock())
    bot = Mock(
        get_me=AsyncMock(return_value=SimpleNamespace(username="example")),
        get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="")),
        session=Mock(close=AsyncMock()),
    )
    llm_session = Mock(close=AsyncMock())
    dispatcher = Mock(
        start_polling=AsyncMock(), resolve_used_update_types=Mock(return_value=["message"])
    )
    runner = Mock(cleanup=AsyncMock())
    migration = AsyncMock()
    maintenance = AsyncMock()
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(application, "create_pool", create_pool)
    monkeypatch.setattr(application, "create_bot", Mock(return_value=bot))
    monkeypatch.setattr(application, "ClientSession", Mock(return_value=llm_session))
    monkeypatch.setattr(application, "Dispatcher", Mock(return_value=dispatcher))
    monkeypatch.setattr(application, "migrate", migration)
    monkeypatch.setattr(application, "maintenance", maintenance)
    monkeypatch.setattr(application, "connect_tools", AsyncMock(return_value=None))
    monkeypatch.setattr(application, "delivery_worker", AsyncMock())
    monkeypatch.setattr(application, "start_health_server", AsyncMock(return_value=runner))
    settings = Settings(bot_token="unused", postgres_password="unused")
    await application.run(settings)
    assert [call.args[0].name for call in dispatcher.include_router.call_args_list] == [
        "temperature",
        "reminders",
        "personal_profile",
        "admin",
        "interface",
        "music",
        "commands",
        "messages",
    ]
    create_pool.assert_awaited_once()
    migration.assert_awaited_once_with(pool)
    maintenance.assert_awaited_once_with(pool, settings)
    assert dispatcher.start_polling.call_args.kwargs["repository"].pool is pool
    dependencies = dispatcher.start_polling.call_args.kwargs
    assert dependencies["operations"].pool is pool
    assert dependencies["assistant"].operations is dependencies["operations"]
    assert dependencies["admin"].billing.session is llm_session
    assert dependencies["assistant"].profiles is dependencies["profiles"]
    assert dependencies["profiles"].retention_days == 180
    pool.close.assert_awaited_once()
    llm_session.close.assert_awaited_once()
    bot.session.close.assert_awaited_once()


def test_missing_llm_setting_exits_before_network(monkeypatch, capsys):
    monkeypatch.setattr(
        application.Settings,
        "load",
        Mock(return_value=Settings(bot_token="unused", postgres_password="unused")),
    )
    create_bot = Mock()
    monkeypatch.setattr(application, "create_bot", create_bot)
    monkeypatch.setattr("sys.argv", ["app"])
    assert application.main() == 1
    assert capsys.readouterr().out == "LLM_BASE_URL: обязательная настройка не задана.\n"
    create_bot.assert_not_called()
