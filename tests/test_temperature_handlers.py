from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, SendDocument, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.admin import DENIED
from app.handlers.temperature import create_router
from tests.test_temperature import service


async def dispatch(test, *, actor=10, chat=10, kind="private", data="temp:open"):
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    user = User(id=actor, is_bot=False, first_name="Student")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat, type=kind),
        from_user=user,
        text="admin",
    )
    query = CallbackQuery(
        id="callback", from_user=user, chat_instance="test", message=message, data=data
    )
    await dispatcher.feed_update(bot, Update(update_id=1, callback_query=query), temperature=test)
    await dispatcher.storage.close()
    return [c.args[1] for c in bot.session.call_args_list]


@pytest.mark.parametrize(
    "actor,chat,kind", [(20, 20, "private"), (10, 20, "private"), (10, -10, "group")]
)
async def test_owner_and_private_chat_required_for_all_controls(actor, chat, kind):
    test = service()
    methods = await dispatch(test, actor=actor, chat=chat, kind=kind)
    assert isinstance(methods[0], AnswerCallbackQuery)
    assert methods[0].text == DENIED
    test.llm.generate.assert_not_awaited()


async def test_preset_previews_paid_calls_and_requires_separate_start():
    test = service()
    methods = await dispatch(test, data="temp:preset")
    messages = [m for m in methods if isinstance(m, SendMessage)]
    assert any("12 платных запросов" in m.text for m in messages)
    assert any("Модель: fixed-model" in m.text for m in messages)
    test.llm.generate.assert_not_awaited()
    last = await test.get(10)
    buttons = [b.callback_data for row in messages[-1].reply_markup.inline_keyboard for b in row]
    assert f"temp:start:{last['id']}" in buttons


async def test_report_delivered_as_files_and_cannot_be_exported_by_non_owner():
    test = service()
    key = await test.prepare(10)
    await test.run(10, key, AsyncMock())
    methods = await dispatch(test, data=f"temp:export:{key}")
    files = [m.document for m in methods if isinstance(m, SendDocument)]
    assert [f.filename for f in files] == ["temperature-report.html", "temperature-results.json"]
    assert b"same answer" in files[0].data
    denied = await dispatch(test, actor=20, chat=20, data=f"temp:export:{key}")
    assert not any(isinstance(m, SendDocument) for m in denied)


async def test_start_uses_service_once_and_repeated_click_makes_no_calls():
    test = service()
    key = await test.prepare(10)
    await dispatch(test, data=f"temp:start:{key}")
    assert test.llm.generate.await_count == 12
    await dispatch(test, data=f"temp:start:{key}")
    assert test.llm.generate.await_count == 12


async def test_custom_prompt_wizard_consumes_text_without_calling_normal_assistant():
    from app.handlers.messages import create_router as messages_router
    from app.handlers.temperature import TemperatureInput

    test = service()
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    dispatcher.include_router(messages_router())
    assistant = AsyncMock()
    user = User(id=10, is_bot=False, first_name="Student")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=10, type="private"),
        from_user=user,
        text="admin",
    )
    callback = CallbackQuery(
        id="custom", from_user=user, chat_instance="test", message=message, data="temp:custom"
    )
    await dispatcher.feed_update(
        bot, Update(update_id=1, callback_query=callback), temperature=test
    )
    state = dispatcher.fsm.get_context(bot=bot, chat_id=10, user_id=10)
    assert await state.get_state() == TemperatureInput.prompt.state
    for number, text in [(2, "x" * 8001), (3, "Придумай три названия трека")]:
        prompt = message.model_copy(update={"message_id": number, "text": text})
        await dispatcher.feed_update(
            bot, Update(update_id=number, message=prompt), temperature=test, assistant=assistant
        )
        if number == 2:
            assert await state.get_state() == TemperatureInput.prompt.state
    assert await state.get_state() is None
    last = await test.get(10)
    assert last["data"]["prompt"] == "Придумай три названия трека"
    assert last["data"]["mode"] == "chat"
    test.llm.generate.assert_not_awaited()
    assistant.respond.assert_not_awaited()
    await dispatcher.storage.close()
