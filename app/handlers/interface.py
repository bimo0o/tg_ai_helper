from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.admin import DENIED, AdminService
from app.assistant import ERROR_MESSAGE
from app.config import Settings
from app.handlers.admin import keyboard as admin_keyboard
from app.music_profile import FIELDS, MusicProfiles
from app.operations import OperationsRepository
from app.prompts import MODES
from app.repository import BUSY_MESSAGE, DialogueBusy, Repository
from app.ui import (
    ADMIN,
    BUTTONS,
    MODE_BUTTONS,
    PROFILE,
    RESET,
    SETTINGS,
    WELCOME,
    back_keyboard,
    confirmation_keyboard,
    main_keyboard,
    profile_keyboard,
    settings_keyboard,
)


class Input(StatesGroup):
    profile_value = State()
    reset_confirmation = State()
    profile_confirmation = State()


async def home(message, actor, state, operations):
    await state.clear()
    owner = await operations.is_admin(actor)
    await message.answer(WELCOME, reply_markup=main_keyboard(owner), parse_mode=None)


async def show_profile(message, actor, profiles):
    values = await profiles.get(actor)
    lines = ["Музыкальный профиль", "Выбери поле, чтобы изменить его.\n"]
    lines.extend(f"{label}: {values.get(key, 'не указано')}" for key, label in FIELDS.items())
    await message.answer("\n".join(lines), reply_markup=profile_keyboard(), parse_mode=None)


async def show_settings(message, actor, repository, settings, operations):
    user = await repository.get_settings((message.chat.id, actor))
    model = await operations.get_model(settings.llm_model)
    owner = await operations.is_admin(actor)
    text = (
        f"Настройки\nРежим: {MODES[user.mode]}\nМодель: {model}\n\n"
        f"Вариативность ответов: {user.temperature:.1f}\n"
        "Меньше — более предсказуемые ответы, больше — больше разнообразия.\n"
        "Выбери значение ниже."
    )
    await message.answer(
        text, reply_markup=settings_keyboard(user.temperature, owner), parse_mode=None
    )


async def show_admin(message, actor, admin):
    view = await admin.overview(actor)
    if view is None:
        await message.answer(DENIED, parse_mode=None)
    else:
        await message.answer(view.text, reply_markup=admin_keyboard(view), parse_mode=None)


async def navigate(
    message: Message,
    state: FSMContext,
    repository: Repository,
    settings: Settings,
    operations: OperationsRepository,
    profiles: MusicProfiles,
    admin: AdminService,
) -> None:
    if message.from_user is None:
        return
    actor = message.from_user.id
    await state.clear()
    try:
        if message.text in MODE_BUTTONS:
            mode = MODE_BUTTONS[message.text]
            async with repository.locked((message.chat.id, actor)) as locked:
                user = await locked.get_settings((message.chat.id, actor))
                changed = user.mode != mode
                if changed:
                    await locked.set_mode((message.chat.id, actor), mode)
            text = f"Режим: {MODES[mode]}. "
            if changed:
                text += "История предыдущего режима очищена. "
            text += (
                "Напиши, что найти. Каждый вопрос запускает платный web-поиск."
                if mode == "web"
                else "Напиши текст и целевой язык."
                if mode == "translate"
                else "Напиши свой вопрос."
            )
            await message.answer(text, parse_mode=None)
        elif message.text == PROFILE:
            await show_profile(message, actor, profiles)
        elif message.text == SETTINGS:
            await show_settings(message, actor, repository, settings, operations)
        elif message.text == RESET:
            await state.set_state(Input.reset_confirmation)
            await message.answer(
                "Очистить историю диалога? Профиль, режим и настройки сохранятся.",
                reply_markup=confirmation_keyboard("reset_yes"),
                parse_mode=None,
            )
        elif message.text == ADMIN:
            await show_admin(message, actor, admin)
        else:
            await home(message, actor, state, operations)
    except DialogueBusy:
        await message.answer(BUSY_MESSAGE, parse_mode=None)
    except Exception:
        await message.answer(ERROR_MESSAGE, parse_mode=None)


async def action(
    callback: CallbackQuery,
    state: FSMContext,
    repository: Repository,
    settings: Settings,
    operations: OperationsRepository,
    profiles: MusicProfiles,
    admin: AdminService,
) -> None:
    message = callback.message
    actor = callback.from_user.id
    if (
        not isinstance(message, Message)
        or message.chat.type != "private"
        or message.chat.id != actor
    ):
        await callback.answer(DENIED, show_alert=True)
        return
    await callback.answer()
    data = (callback.data or "")[3:]
    try:
        if data == "home":
            await home(message, actor, state, operations)
        elif data.startswith("field:") and data[6:] in FIELDS:
            field = data[6:]
            await state.set_state(Input.profile_value)
            await state.update_data(field=field)
            await message.answer(
                f"{FIELDS[field]}: напиши новое значение (до 240 символов).",
                reply_markup=back_keyboard(),
                parse_mode=None,
            )
        elif data.startswith("temp:") and data[5:] in {"0.0", "0.3", "0.7", "1.0"}:
            await state.clear()
            await repository.set_temperature((message.chat.id, actor), float(data[5:]))
            await show_settings(message, actor, repository, settings, operations)
        elif data == "admin":
            await state.clear()
            await show_admin(message, actor, admin)
        elif data == "profile_clear":
            await state.set_state(Input.profile_confirmation)
            await message.answer(
                "Удалить музыкальный профиль? История диалога сохранится.",
                reply_markup=confirmation_keyboard("profile_yes"),
                parse_mode=None,
            )
        elif data == "profile_yes" and await state.get_state() == Input.profile_confirmation.state:
            await profiles.clear(actor)
            await state.clear()
            await show_profile(message, actor, profiles)
        elif data == "reset_yes" and await state.get_state() == Input.reset_confirmation.state:
            async with repository.locked((message.chat.id, actor)) as locked:
                await locked.clear_history((message.chat.id, actor))
            await state.clear()
            await message.answer("История очищена. Профиль и настройки сохранены.", parse_mode=None)
        else:
            await message.answer("Действие устарело. Открой меню заново.", parse_mode=None)
    except DialogueBusy:
        await message.answer(BUSY_MESSAGE, parse_mode=None)
    except Exception:
        await message.answer(ERROR_MESSAGE, parse_mode=None)


async def profile_value(message: Message, state: FSMContext, profiles: MusicProfiles) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    field = data.get("field")
    if field not in FIELDS:
        await state.clear()
        await message.answer("Выбери поле профиля заново.", parse_mode=None)
        return
    try:
        await profiles.set_field(message.from_user.id, field, message.text)
    except ValueError:
        await message.answer(
            "Нужно непустое значение до 240 символов. Попробуй ещё раз.", parse_mode=None
        )
        return
    except Exception:
        await message.answer(ERROR_MESSAGE, parse_mode=None)
        return
    await state.clear()
    await show_profile(message, message.from_user.id, profiles)


def create_router() -> Router:
    router = Router(name="interface")
    router.message.filter(F.chat.type == "private", F.text)
    router.message.register(navigate, F.text.in_(BUTTONS))
    router.message.register(navigate, Command("cancel"))
    router.message.register(
        profile_value, Input.profile_value, ~F.text.startswith("/"), ~F.text.in_(BUTTONS)
    )
    router.callback_query.register(action, F.data.startswith("ui:"))
    return router
