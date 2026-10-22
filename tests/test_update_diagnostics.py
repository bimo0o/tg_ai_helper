from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Chat, Document, Message, PhotoSize, Update, User

from app.assistant import Reply
from app.handlers.messages import create_router
from app.update_diagnostics import UpdateDiagnostics


async def dispatch(*, kind="text", chat_type="private"):
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(UpdateDiagnostics())
    dispatcher.include_router(create_router())
    assistant = AsyncMock()
    assistant.respond.return_value = Reply("fixture answer", ok=True)
    text = "PRIVATE_TEST_TEXT " * 200
    fields = {"text": text}
    if kind == "document":
        fields = {
            "document": Document(
                file_id="test", file_unique_id="test", file_name="PRIVATE_FILENAME.txt"
            ),
            "caption": text,
        }
    elif kind == "photo":
        fields = {"photo": [PhotoSize(file_id="test", file_unique_id="test", width=1, height=1)]}
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=10, type=chat_type),
        from_user=User(id=10, is_bot=False, first_name="PRIVATE_NAME"),
        **fields,
    )
    update = Update(update_id=1, **{"edited_message" if kind == "edited" else "message": message})
    result = await dispatcher.feed_update(bot, update, assistant=assistant)
    await dispatcher.storage.close()
    return assistant, [c.args[1] for c in bot.session.call_args_list], result, text


async def test_long_private_text_reaches_assistant_and_does_not_log_as_unhandled(caplog):
    assistant, methods, _, text = await dispatch()
    assistant.respond.assert_awaited_once_with((10, 10), text)
    assert any(
        isinstance(method, SendMessage) and method.text == "fixture answer" for method in methods
    )
    assert "Unhandled update" not in caplog.text


@pytest.mark.parametrize(
    "kind,chat_type,event_type,content_type",
    [
        ("document", "private", "message", "document"),
        ("photo", "private", "message", "photo"),
        ("text", "group", "message", "text"),
        ("edited", "private", "edited_message", "text"),
    ],
)
async def test_unsupported_updates_log_type_but_not_private_data(
    kind,
    chat_type,
    event_type,
    content_type,
    caplog,
):
    assistant, methods, _, _ = await dispatch(kind=kind, chat_type=chat_type)
    assistant.respond.assert_not_awaited()
    assert methods == []
    assert f"event_type={event_type}" in caplog.text
    assert f"chat_type={chat_type}" in caplog.text
    assert f"content_type={content_type}" in caplog.text
    assert "PRIVATE_TEST_TEXT" not in caplog.text
    assert "PRIVATE_FILENAME" not in caplog.text and "PRIVATE_NAME" not in caplog.text
    assert "user_id" not in caplog.text and "chat_id" not in caplog.text
