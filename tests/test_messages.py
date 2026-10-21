import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendChatAction, SendMessage
from aiogram.types import Chat, Message, PhotoSize, Update, User

from app.assistant import ERROR_MESSAGE
from app.handlers.commands import create_router as commands_router
from app.handlers.messages import UNKNOWN_COMMAND
from app.handlers.messages import create_router as messages_router
from tests.test_assistant import assistant
from tests.test_llm import response


async def dispatch(text=None, *, data=None, error=None, chat_type="private", photo=None):
    service, pool, http = assistant(data, error=error)
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    dispatcher = Dispatcher()
    dispatcher.include_router(commands_router())
    dispatcher.include_router(messages_router())
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=1, type=chat_type),
        from_user=User(id=1, is_bot=False, first_name="Student"),
        text=text,
        photo=photo,
    )
    await dispatcher.feed_update(
        bot,
        Update(update_id=1, message=message),
        assistant=service,
        repository=service.repository,
        settings=service.settings,
    )
    methods = [call.args[1] for call in bot.session.call_args_list]
    return methods, service, pool, http


async def test_long_response_is_sent_in_order_without_any_text_loss():
    text = ("<b>текст</b> & *слово* 🎸\n " * 1000) + "end"
    methods, _, _, _ = await dispatch("вопрос", data=response(text))
    sent = [method for method in methods if isinstance(method, SendMessage)]
    assert len(sent) > 1
    assert "".join(method.text for method in sent) == text
    assert all(method.parse_mode is None and len(method.text) <= 4096 for method in sent)


async def test_error_returns_exact_safe_text():
    methods, _, pool, _ = await dispatch("вопрос", error=TimeoutError("example-private-details"))
    sent = [method for method in methods if isinstance(method, SendMessage)]
    assert [method.text for method in sent] == [ERROR_MESSAGE]
    assert [row["role"] for row in pool.messages] == ["user"]


@pytest.mark.parametrize(
    "command", ["/start", "/study", "/translate", "/music", "/reset", "/settings 0.7"]
)
async def test_commands_never_reach_llm(command):
    methods, _, _, http = await dispatch(command)
    http.post.assert_not_called()
    assert any(isinstance(method, SendMessage) for method in methods)


async def test_unknown_command_is_not_sent_to_llm():
    methods, _, _, http = await dispatch("/unknown")
    http.post.assert_not_called()
    assert methods[0].text == UNKNOWN_COMMAND


async def test_group_and_photo_are_ignored():
    for kwargs in [
        {"text": "hello", "chat_type": "group"},
        {"photo": [PhotoSize(file_id="a", file_unique_id="b", width=1, height=1)]},
    ]:
        methods, _, pool, http = await dispatch(**kwargs)
        assert methods == [] and pool.calls == []
        http.post.assert_not_called()


async def test_typing_is_sent_while_generation_is_pending(monkeypatch):
    from app.llm import LLMClient

    original = LLMClient.generate

    async def delayed(self, *args, **kwargs):
        await asyncio.sleep(0.02)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(LLMClient, "generate", delayed)
    methods, _, _, _ = await dispatch("вопрос")
    assert any(
        isinstance(method, SendChatAction) and method.action == "typing" for method in methods
    )
