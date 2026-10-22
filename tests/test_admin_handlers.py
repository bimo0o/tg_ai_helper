from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.admin import DENIED, AdminService
from app.handlers.admin import create_router
from app.operations import MODEL_OPTIONS, OperationsRepository
from tests.fakes import MemoryPool
from tests.test_billing import client


async def dispatch(
    *,
    user_id=10,
    owner_id=10,
    data=None,
    chat_type="private",
    pool=None,
    edit_error=None,
    ack_error=None,
):
    pool = pool or MemoryPool()
    operations = OperationsRepository(pool)
    if owner_id is not None:
        await operations.assign_owner(owner_id)
    billing, http = client({"data": {"usage": 1, "limit": 10, "limit_remaining": 9}})
    service = AdminService(operations, billing, "default-model")
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    if edit_error or ack_error:

        async def api(bot, method, **kwargs):
            if isinstance(method, EditMessageText) and edit_error:
                raise TelegramBadRequest(method=method, message=edit_error)
            if isinstance(method, AnswerCallbackQuery) and ack_error:
                raise TelegramBadRequest(method=method, message=ack_error)
            return True

        bot.session.side_effect = api
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    user = User(id=user_id, is_bot=False, first_name="Student")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type=chat_type),
        from_user=user,
        text="/admin",
    )
    if data:
        update = Update(
            update_id=1,
            callback_query=CallbackQuery(
                id="callback",
                from_user=user,
                chat_instance="example",
                message=message,
                data=data,
            ),
        )
    else:
        update = Update(update_id=1, message=message)
    await dispatcher.feed_update(bot, update, admin=service)
    return [call.args[1] for call in bot.session.call_args_list], pool, http


async def test_admin_command_shows_panel_only_to_db_owner():
    methods, _, _ = await dispatch()
    assert isinstance(methods[0], SendMessage)
    assert methods[0].text.startswith("Админ-панель")
    callbacks = [b.callback_data for row in methods[0].reply_markup.inline_keyboard for b in row]
    assert "adm:model:qwen" in callbacks and "adm:model:deepseek" in callbacks
    methods, _, http = await dispatch(user_id=20)
    assert methods[0].text == DENIED
    http.get.assert_not_called()


async def test_copied_callback_cannot_change_model_or_fetch_budget():
    methods, pool, http = await dispatch(user_id=20, data="adm:model:deepseek")
    assert pool.model is None
    assert isinstance(methods[0], AnswerCallbackQuery) and methods[0].text == DENIED
    http.get.assert_not_called()


@pytest.mark.parametrize("alias", ["qwen", "deepseek"])
async def test_owner_selects_allowed_model_and_it_persists(alias):
    methods, pool, _ = await dispatch(data=f"adm:model:{alias}")
    assert await OperationsRepository(pool).get_model("default") == MODEL_OPTIONS[alias]
    edited = [method for method in methods if isinstance(method, EditMessageText)]
    assert MODEL_OPTIONS[alias] in edited[0].text


@pytest.mark.parametrize("data", ["adm:model:unknown", "adm:view:all:-10", "adm:view:no:0"])
async def test_invalid_callback_does_not_change_model(data):
    methods, pool, _ = await dispatch(data=data)
    assert pool.model is None
    assert any(
        isinstance(method, SendMessage) and method.text.startswith("Некорректное действие")
        for method in methods
    )


async def test_no_owner_means_no_public_bootstrap_and_group_callbacks_are_denied():
    methods, _, http = await dispatch(owner_id=None)
    assert methods[0].text == DENIED
    http.get.assert_not_called()


async def test_refresh_not_modified_does_not_send_a_false_error():
    methods, _, _ = await dispatch(
        data="adm:view:day:0", edit_error="Bad Request: message is not modified"
    )
    assert any(isinstance(method, EditMessageText) for method in methods)
    assert not any(isinstance(method, SendMessage) for method in methods)


async def test_group_callbacks_are_denied_before_model_or_billing_access():
    methods, pool, http = await dispatch(data="adm:model:qwen", chat_type="group")
    assert pool.model is None and methods[0].text == DENIED
    http.get.assert_not_called()


@pytest.mark.parametrize("reason", ["message can't be edited", "message to edit not found"])
async def test_uneditable_panel_is_replaced_with_a_new_panel(reason):
    methods, _, _ = await dispatch(data="adm:view:day:0", edit_error=reason)
    messages = [method for method in methods if isinstance(method, SendMessage)]
    assert len(messages) == 1 and messages[0].text.startswith("Админ-панель")
    assert messages[0].reply_markup is not None


async def test_unexpected_edit_error_is_reported_as_admin_error_and_logged_safely(caplog):
    from app.handlers.admin import ADMIN_ERROR

    methods, _, _ = await dispatch(data="adm:view:day:0", edit_error="other telegram error")
    messages = [method for method in methods if isinstance(method, SendMessage)]
    assert len(messages) == 1 and messages[0].text == ADMIN_ERROR
    assert "stage=edit error_type=TelegramBadRequest" in caplog.text
    assert "other telegram error" not in caplog.text


@pytest.mark.parametrize(
    "reason", ["query is too old and response timeout expired", "query ID is invalid"]
)
async def test_expired_callback_acknowledgement_does_not_prevent_authorized_refresh(reason):
    methods, _, _ = await dispatch(data="adm:view:day:0", ack_error=reason)
    assert any(isinstance(method, EditMessageText) for method in methods)
    assert not any(isinstance(method, SendMessage) for method in methods)


async def test_unchanged_panel_is_not_sent_to_telegram():
    from app.admin import AdminView
    from app.handlers.admin import keyboard, update_view

    view = AdminView("unchanged", "day", 0, False)
    message = AsyncMock()
    message.text = view.text
    message.reply_markup = keyboard(view)
    await update_view(message, view)
    message.edit_text.assert_not_awaited()
    message.answer.assert_not_awaited()
