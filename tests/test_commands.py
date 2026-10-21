from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from app.config import Settings
from app.handlers.commands import START_MESSAGE, create_router
from app.repository import Repository, UserSettings
from tests.fakes import MemoryPool


async def dispatch(text, pool=None, chat_type="private", **dependencies):
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    repository = Repository(pool if pool is not None else MemoryPool())
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=1, type=chat_type),
        from_user=User(id=1, is_bot=False, first_name="Student"),
        text=text,
    )
    await dispatcher.feed_update(
        bot, Update(update_id=1, message=message), repository=repository, **dependencies
    )
    return bot, repository


async def test_start_describes_exact_commands_without_api():
    bot, _ = await dispatch("/start")
    assert bot.session.call_args.args[1].text == START_MESSAGE


async def test_switch_mode_clears_history_and_confirms_exact_text():
    pool = MemoryPool()
    repo = Repository(pool)
    await repo.get_settings((1, 1))
    await repo.add_message((1, 1), "user", "old", "study")
    bot, repository = await dispatch("/music", pool)
    assert await repository.get_settings((1, 1)) == UserSettings("music", 0.3)
    assert pool.messages == []
    assert bot.session.call_args.args[1].text == "Включён режим «Создание музыки». История очищена."


async def test_commands_ignore_group_chats():
    bot, repo = await dispatch("/start", chat_type="group")
    bot.session.assert_not_called()
    assert repo.pool.calls == []


@pytest.mark.parametrize("temperature", ["0.0", "0.3", "0.7", "1.0"])
async def test_settings_accepts_each_supported_value_and_isolates_users(temperature):
    pool = MemoryPool()
    config = Settings(bot_token="unused", postgres_password="unused", llm_model="test-model")
    bot, repo = await dispatch(f"/settings {temperature}", pool, settings=config)
    assert (await repo.get_settings((1, 1))).temperature == float(temperature)
    assert (await repo.get_settings((2, 2))).temperature == 0.3
    assert bot.session.call_args.args[1].text == (
        "Режим: /study (Учёба)\nМодель: test-model\n"
        f"Temperature: {temperature}\nИзменить: /settings 0.0|0.3|0.7|1.0"
    )


@pytest.mark.parametrize("value", ["0.4", "nan", "-1", "0,3", "1", "0.3 extra"])
async def test_settings_rejects_invalid_values_before_database(value):
    config = Settings(bot_token="unused", postgres_password="unused", llm_model="test-model")
    bot, repo = await dispatch(f"/settings {value}", settings=config)
    assert repo.pool.calls == []
    assert bot.session.call_args.args[1].text == (
        "Допустимые значения temperature: 0.0, 0.3, 0.7, 1.0. Пример: /settings 0.3"
    )
