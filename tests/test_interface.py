from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.admin import DENIED, AdminService
from app.handlers.admin import create_router as admin_router
from app.handlers.commands import create_router as commands_router
from app.handlers.interface import create_router as interface_router
from app.handlers.messages import create_router as messages_router
from app.handlers.music import create_router as music_router
from app.handlers.personal_profile import create_router as personal_router
from app.llm import Generation
from app.music_profile import MusicProfiles
from app.operations import OperationsRepository
from app.personal_profile import PersonalProfiles
from app.ui import ADMIN, MENU, MODE_BUTTONS, PROFILE, RESET, SETTINGS
from tests.test_assistant import assistant
from tests.test_billing import client as billing_client


async def interface():
    service, pool, http = assistant()
    operations = OperationsRepository(pool)
    await operations.assign_owner(1)
    profiles = MusicProfiles(pool)
    service.profiles = profiles
    service.operations = operations
    summarizer = AsyncMock()
    summarizer.generate.return_value = Generation(True, '{"summary":"Учу Python, люблю примеры"}')
    personal = PersonalProfiles(pool, summarizer, operations, service.settings)
    service.personal_profiles = personal
    billing, _ = billing_client({})
    admin = AdminService(operations, billing, "fallback")
    bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock()
    dispatcher = Dispatcher()
    for router in (
        admin_router(),
        personal_router(),
        interface_router(),
        music_router(),
        commands_router(),
        messages_router(),
    ):
        dispatcher.include_router(router)
    number = 0

    async def send(text=None, *, callback=None, user_id=1, chat_id=None, chat_type="private"):
        nonlocal number
        number += 1
        bot.session.reset_mock()
        user = User(id=user_id, is_bot=False, first_name="Test")
        message = Message(
            message_id=number,
            date=datetime.now(UTC),
            chat=Chat(id=user_id if chat_id is None else chat_id, type=chat_type),
            from_user=User(id=999, is_bot=True, first_name="Bot") if callback else user,
            text=text or "panel",
        )
        update = (
            Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=str(number),
                    from_user=user,
                    chat_instance="test",
                    message=message,
                    data=callback,
                ),
            )
            if callback
            else Update(update_id=number, message=message)
        )
        await dispatcher.feed_update(
            bot,
            update,
            repository=service.repository,
            assistant=service,
            settings=service.settings,
            operations=operations,
            profiles=profiles,
            personal_profiles=personal,
            admin=admin,
        )
        return [call.args[1] for call in bot.session.call_args_list]

    return send, service, pool, http


async def test_non_owner_has_no_admin_button_in_settings():
    send, _, _, _ = await interface()
    methods = await send(SETTINGS, user_id=2)
    assert "ui:admin" not in {
        b.callback_data for row in methods[-1].reply_markup.inline_keyboard for b in row
    }
    methods = await send(SETTINGS)
    assert "ui:admin" in {
        b.callback_data for row in methods[-1].reply_markup.inline_keyboard for b in row
    }


async def test_personal_profile_requires_current_owner_confirmation():
    send, service, pool, _ = await interface()
    await send(callback="pm:edit", user_id=2)
    methods = await send("Я учу Python, люблю примеры", user_id=2)
    confirm = methods[-1].reply_markup.inline_keyboard[0][0].callback_data
    assert confirm.startswith("pm:confirm:")
    assert await service.personal_profiles.get(2) == ""
    await send(callback=confirm, user_id=1)
    assert await service.personal_profiles.get(1) == ""
    await send(callback=confirm, user_id=2)
    assert await service.personal_profiles.get(2) == "Учу Python, люблю примеры"
    assert pool.messages == []
    await send(callback="pm:delete", user_id=2)
    methods = await send(callback="pm:delete", user_id=2)
    deletion = methods[-1].reply_markup.inline_keyboard[0][0].callback_data
    await send(callback=deletion, user_id=2)
    assert await service.personal_profiles.get(2) == ""


async def test_personal_profile_cancel_invalidates_suggestion():
    send, service, _, _ = await interface()
    await send(callback="pm:edit")
    methods = await send("Я изучаю Python")
    confirm = methods[-1].reply_markup.inline_keyboard[0][0].callback_data
    await send(MENU)
    await send(callback=confirm)
    assert await service.personal_profiles.get(1) == ""


async def test_personal_profile_confirmation_expires(monkeypatch):
    import time
    from types import SimpleNamespace

    from app.handlers import personal_profile

    send, service, _, _ = await interface()
    await send(callback="pm:edit")
    methods = await send("Я изучаю Python")
    confirm = methods[-1].reply_markup.inline_keyboard[0][0].callback_data
    later = time.monotonic() + 301
    monkeypatch.setattr(personal_profile, "time", SimpleNamespace(monotonic=lambda: later))
    await send(callback=confirm)
    assert await service.personal_profiles.get(1) == ""


async def test_personal_profile_callbacks_cannot_read_or_edit_another_private_chat():
    send, service, _, _ = await interface()
    await service.personal_profiles.save(1, "private profile")
    for callback in ("pm:open", "pm:edit", "pm:delete"):
        methods = await send(callback=callback, user_id=2, chat_id=1)
        assert methods[0].text == DENIED
        assert len(methods) == 1
    assert await service.personal_profiles.get(1) == "private profile"


async def test_start_shows_persistent_keyboard_and_admin_button_only_to_owner():
    send, _, _, http = await interface()
    methods = await send("/start")
    keyboard = methods[-1].reply_markup
    assert keyboard.is_persistent and keyboard.resize_keyboard
    assert ADMIN in {button.text for row in keyboard.keyboard for button in row}
    methods = await send("/start", user_id=2)
    assert ADMIN not in {button.text for row in methods[-1].reply_markup.keyboard for button in row}
    http.post.assert_not_called()


@pytest.mark.parametrize("label,mode", list(MODE_BUTTONS.items()), ids=list(MODE_BUTTONS.values()))
async def test_mode_buttons_change_mode_without_calling_model(label, mode):
    send, service, _, http = await interface()
    await send(label)
    assert (await service.repository.get_settings((1, 1))).mode == mode
    http.post.assert_not_called()


async def test_pressing_active_mode_does_not_clear_history_again():
    send, service, pool, _ = await interface()
    await send("🎵 Музыка")
    await service.repository.add_message((1, 1), "user", "keep this", "music")
    await send("🎵 Музыка")
    assert pool.messages[0]["content"] == "keep this"


async def test_profile_wizard_uses_clicking_user_and_does_not_send_value_to_model():
    send, service, _, http = await interface()
    await send(PROFILE)
    await send(callback="ui:field:daw")
    await send("Ableton Live")
    assert await service.profiles.get(1) == {"daw": "Ableton Live"}
    assert await service.profiles.get(999) == {}
    http.post.assert_not_called()


async def test_profile_input_is_isolated_between_users_and_validated():
    send, service, _, http = await interface()
    await send(callback="ui:field:daw")
    await send("question", user_id=2)
    assert await service.profiles.get(1) == {}
    assert http.post.call_count == 1
    methods = await send("x" * 241)
    assert "до 240" in methods[-1].text
    await send("Live")
    assert await service.profiles.get(1) == {"daw": "Live"}
    assert http.post.call_count == 1


async def test_settings_callback_changes_actor_settings_not_the_bot_settings():
    send, service, _, http = await interface()
    await send(SETTINGS)
    await send(callback="ui:temp:0.7")
    assert (await service.repository.get_settings((1, 1))).temperature == 0.7
    assert (await service.repository.get_settings((999, 999))).temperature == 0.3
    await send(callback="ui:temp:nan")
    assert (await service.repository.get_settings((1, 1))).temperature == 0.7
    http.post.assert_not_called()


async def test_reset_requires_current_confirmation_and_preserves_profile_and_other_users():
    send, service, pool, http = await interface()
    for owner in [(1, 1), (2, 2)]:
        await service.repository.get_settings(owner)
        await service.repository.add_message(owner, "user", "saved", "study")
    await service.profiles.set_field(1, "daw", "Live")
    await send(RESET)
    assert len(pool.messages) == 2
    await send(MENU)
    await send(callback="ui:reset_yes")
    assert len(pool.messages) == 2
    await send(RESET)
    await send(callback="ui:reset_yes")
    assert len(pool.messages) == 1 and pool.messages[0]["owner"] == (2, 2)
    assert await service.profiles.get(1) == {"daw": "Live"}
    http.post.assert_not_called()


async def test_profile_clear_needs_confirmation_and_keeps_history():
    send, service, pool, _ = await interface()
    await service.repository.get_settings((1, 1))
    await service.repository.add_message((1, 1), "user", "saved", "study")
    await service.profiles.set_field(1, "daw", "Live")
    await send(callback="ui:profile_clear")
    assert await service.profiles.get(1) == {"daw": "Live"}
    await send(callback="ui:profile_yes")
    assert await service.profiles.get(1) == {} and len(pool.messages) == 1


async def test_commands_cancel_pending_input_so_questions_are_not_saved_as_profile():
    send, service, _, http = await interface()
    await send(callback="ui:field:daw")
    await send("/music")
    await send("How do I record bass?")
    assert await service.profiles.get(1) == {}
    assert http.post.call_count == 1


async def test_opening_admin_also_cancels_pending_profile_input():
    send, service, _, http = await interface()
    await send(callback="ui:field:daw")
    await send("/admin")
    await send("ordinary question")
    assert await service.profiles.get(1) == {}
    assert http.post.call_count == 1


async def test_admin_buttons_and_cross_chat_callbacks_cannot_bypass_authorization():
    send, service, _, http = await interface()
    methods = await send(ADMIN, user_id=2)
    assert methods[-1].text == DENIED
    methods = await send(callback="ui:admin", user_id=2)
    assert [method.text for method in methods if isinstance(method, SendMessage)] == [DENIED]
    methods = await send(callback="ui:temp:1.0", user_id=1, chat_id=2)
    assert any(isinstance(method, AnswerCallbackQuery) and method.show_alert for method in methods)
    assert (await service.repository.get_settings((1, 1))).temperature == 0.3
    methods = await send(PROFILE, chat_type="group")
    assert methods == []
    http.post.assert_not_called()
