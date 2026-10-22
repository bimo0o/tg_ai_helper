from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.handlers.reminders import create_router
from app.reminders import ReminderUnavailable
from app.ui import REMINDERS


def fixture():
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    reminders = AsyncMock()
    reminders.get_timezone.return_value = "UTC"
    reminders.list.return_value = []
    reminders.clock = lambda: datetime(2026, 10, 22, tzinfo=UTC)
    tools = AsyncMock()
    tools.call.return_value = {"created": True}
    return bot, dispatcher, reminders, tools


async def feed(fixture, text=None, callback=None, actor=1):
    bot, dispatcher, reminders, tools = fixture
    user = User(id=actor, is_bot=False, first_name="Synthetic")
    message = Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        from_user=user,
        text=text or "panel",
    )
    event = (
        Update(
            update_id=10,
            callback_query=CallbackQuery(
                id="test", from_user=user, chat_instance="test", message=message, data=callback
            ),
        )
        if callback
        else Update(update_id=10, message=message)
    )
    bot.session.reset_mock()
    await dispatcher.feed_update(bot, event, reminders=reminders, tools=tools)
    return [
        call.args[1] for call in bot.session.call_args_list if isinstance(call.args[1], SendMessage)
    ]


async def test_manual_creation_previews_before_mcp_and_binds_confirmed_owner():
    context = fixture()
    _, dispatcher, reminders, tools = context
    action = {
        "id": uuid4(),
        "text": "Synthetic task",
        "timezone": "UTC",
        "remind_at": datetime(2030, 1, 10, 10, tzinfo=UTC),
    }
    reminders.propose.return_value = action
    reminders.confirm.return_value = action
    assert (await feed(context, text=REMINDERS))[0].text.startswith("⏰ Напоминания")
    await feed(context, callback="rm:new")
    await feed(context, text="Synthetic task")
    messages = await feed(context, text="2030-01-10 10:00")
    assert "Пока ничего не запланировано" in messages[0].text
    tools.call.assert_not_awaited()
    await feed(context, callback=f"rm:confirm:{action['id']}")
    tools.call.assert_awaited_once_with(
        "add_reminder",
        {"text": action["text"], "remind_at": action["remind_at"].isoformat()},
        owner=(1, 1),
        action_id=action["id"],
    )
    await dispatcher.storage.close()


async def test_foreign_and_expired_confirmation_never_calls_mcp():
    context = fixture()
    _, dispatcher, reminders, tools = context
    key = uuid4()
    await feed(context, callback=f"rm:confirm:{key}", actor=2)
    reminders.confirm.assert_not_awaited()
    reminders.confirm.side_effect = ReminderUnavailable
    messages = await feed(context, callback=f"rm:confirm:{key}")
    assert "истекло" in messages[0].text
    tools.call.assert_not_awaited()
    await dispatcher.storage.close()


async def test_timezone_required_and_tools_discovery_is_not_hardcoded():
    context = fixture()
    _, dispatcher, reminders, tools = context
    reminders.get_timezone.return_value = None
    messages = await feed(context, callback="rm:new")
    assert "Сначала задай часовой пояс" in messages[0].text
    reminders.propose.assert_not_awaited()
    from types import SimpleNamespace

    tools.discover.return_value = [SimpleNamespace(name="fixture_tool", description="From MCP")]
    messages = await feed(context, text="/tools")
    assert "fixture_tool" in messages[0].text and "From MCP" in messages[0].text
    await dispatcher.storage.close()
