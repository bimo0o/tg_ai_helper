from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from app.handlers.music import PROFILE_HELP, create_router
from app.music_profile import MusicProfiles
from app.repository import Repository
from tests.test_assistant import assistant


async def dispatch(text, pool=None):
    service, initial_pool, http = assistant()
    if pool is not None:
        service.repository.pool = pool
    else:
        pool = initial_pool
    profiles = MusicProfiles(pool)
    service.profiles = profiles
    if pool is initial_pool:
        await service.repository.set_mode((1, 1), "music")
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="Student"),
        text=text,
    )
    await dispatcher.feed_update(
        bot, Update(update_id=1, message=message), assistant=service, profiles=profiles
    )
    sent = [
        call.args[1] for call in bot.session.call_args_list if isinstance(call.args[1], SendMessage)
    ]
    return sent, pool, http


async def test_profile_commands_persist_show_and_remove_data_without_llm():
    sent, pool, http = await dispatch("/profile daw Ableton Live")
    assert sent[0].text == "Сохранено поле «DAW»."
    http.post.assert_not_called()
    await Repository(pool).add_message((1, 1), "user", "saved history", "music")
    sent, _, _ = await dispatch("/profile", pool)
    assert "DAW: Ableton Live" in sent[0].text
    sent, _, _ = await dispatch("/profile clear", pool)
    assert sent[0].text == "Музыкальный профиль удалён. История диалога сохранена."
    assert await MusicProfiles(pool).get(1) == {}
    assert pool.messages[0]["content"] == "saved history"


async def test_profile_rejects_unknown_fields_and_search_requires_query():
    sent, _, http = await dispatch("/profile secret value")
    assert sent[0].text == PROFILE_HELP
    http.post.assert_not_called()
    sent, _, http = await dispatch("/search")
    assert sent[0].text == "В режиме /music используйте: /search <вопрос для web-поиска>."
    http.post.assert_not_called()


async def test_search_command_enables_web_and_does_not_send_command_prefix():
    _, _, http = await dispatch("/search Найди документацию синтезатора")
    payload = http.post.call_args.kwargs["json"]
    assert payload["plugins"][0]["id"] == "web"
    assert payload["messages"][-1]["content"] == "Найди документацию синтезатора"
