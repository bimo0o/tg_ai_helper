from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.admin import AdminService
from app.assistant import Assistant
from app.handlers.admin import create_router as admin_router
from app.handlers.commands import create_router as commands_router
from app.handlers.messages import create_router as messages_router
from app.handlers.music import create_router as music_router
from app.music_profile import MusicProfiles
from app.operations import MODEL_OPTIONS, OperationsRepository
from tests.test_assistant import assistant
from tests.test_billing import client as billing_client


async def test_model_button_changes_actual_next_generation_and_reset_keeps_all_user_metrics():
    service, pool, http = assistant()
    operations = OperationsRepository(pool)
    await operations.assign_owner(10)
    await operations.set_model(10, "qwen")
    profiles = MusicProfiles(pool)
    service = Assistant(service.repository, service.llm, service.settings, operations, profiles)
    billing, _ = billing_client({})
    admin = AdminService(operations, billing, service.settings.llm_model)
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    dispatcher = Dispatcher()
    for router in (admin_router(), music_router(), commands_router(), messages_router()):
        dispatcher.include_router(router)
    number = 0

    async def send(user_id, text, callback=None):
        nonlocal number
        number += 1
        user = User(id=user_id, is_bot=False, first_name="Student")
        message = Message(
            message_id=number,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=user,
            text=text,
        )
        if callback:
            update = Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=f"cb-{number}",
                    from_user=user,
                    chat_instance="example",
                    message=message,
                    data=callback,
                ),
            )
        else:
            update = Update(update_id=number, message=message)
        await dispatcher.feed_update(
            bot,
            update,
            assistant=service,
            repository=service.repository,
            settings=service.settings,
            operations=operations,
            admin=admin,
            profiles=profiles,
        )

    await send(1, "first question")
    assert http.post.call_args.kwargs["json"]["model"] == MODEL_OPTIONS["qwen"]
    await send(10, "panel", callback="adm:model:deepseek")
    assert http.post.call_count == 1
    await send(2, "/music")
    await send(2, "/profile daw Ableton Live")
    await send(2, "second question")
    assert http.post.call_args.kwargs["json"]["model"] == MODEL_OPTIONS["deepseek"]
    assert "plugins" not in http.post.call_args.kwargs["json"]
    assert "Ableton Live" in str(http.post.call_args.kwargs["json"]["messages"])
    await send(2, "/search Найди документацию")
    assert http.post.call_args.kwargs["json"]["plugins"][0]["id"] == "web"
    await send(2, "/settings")
    sent = [
        call.args[1] for call in bot.session.call_args_list if isinstance(call.args[1], SendMessage)
    ]
    assert MODEL_OPTIONS["deepseek"] in sent[-1].text
    await send(1, "/reset")
    await send(2, "/reset")
    assert await profiles.get(2) == {"daw": "Ableton Live"}
    await send(2, "third music question")
    await send(2, "rate-limited question")
    sent = [
        call.args[1] for call in bot.session.call_args_list if isinstance(call.args[1], SendMessage)
    ]
    assert sent[-1].text == "Слишком много запросов. Подождите минуту и попробуйте снова."
    assert len(pool.events) == 4 and {row["user_id"] for row in pool.events.values()} == {1, 2}
    await send(10, "/admin")
    sent = [
        call.args[1] for call in bot.session.call_args_list if isinstance(call.args[1], SendMessage)
    ]
    assert "Запросов к LLM: 4; пользователей: 2" in sent[-1].text
    assert http.post.call_count == 4
