import logging
import time
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from app.admin import DENIED
from app.personal_profile import MAX_INPUT, PersonalProfiles
from app.ui import BUTTONS

logger = logging.getLogger(__name__)


class PersonalInput(StatesGroup):
    description = State()
    generating = State()
    confirmation = State()


def keyboard(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


async def show(message, actor, profiles):
    summary = await profiles.get(actor)
    await message.answer(
        "Обо мне · персонализация\n\n"
        + (summary or "Пока ничего не сохранено.")
        + "\n\nСводка до 700 символов применяется в диалоге, поиске, учёбе и музыке, "
        "когда уместна. Перевод и тесты температур её не используют. "
        "Хранение — 180 дней без использования. «Новый диалог» её сохраняет.",
        reply_markup=keyboard(
            [
                [("Задать / заменить сведения", "pm:edit")],
                [("Удалить сведения", "pm:delete")],
                [("В меню", "ui:home")],
            ]
        ),
        parse_mode=None,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def action(callback: CallbackQuery, state: FSMContext, personal_profiles: PersonalProfiles):
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
    command = (callback.data or "")[3:]
    try:
        if command == "open":
            await state.clear()
            await show(message, actor, personal_profiles)
        elif command == "edit":
            await state.clear()
            await state.set_state(PersonalInput.description)
            await message.answer(
                f"Расскажи об интересах, целях, опыте и предпочтениях к ответам "
                f"(до {MAX_INPUT} символов). Новое описание заменит прежнее.\n\n"
                "После отправки будет один платный запрос к модели для сжатия. "
                "Перед сохранением проверь и подтверди сводку. Исходное описание в БД "
                "не сохраняется. Не указывай ключи, пароли и контактные данные. "
                "Выход: /cancel или «Меню».",
                parse_mode=None,
            )
        elif command == "delete":
            token = uuid.uuid4().hex
            await state.set_state(PersonalInput.confirmation)
            await state.set_data({"token": token, "kind": "delete", "at": time.monotonic()})
            await message.answer(
                "Удалить сведения «Обо мне»? Музыкальный профиль и история сохранятся. "
                "Если сведения упоминались в диалоге, отдельно очисти историю.",
                reply_markup=keyboard(
                    [[("Удалить", f"pm:confirm:{token}"), ("Отмена", "pm:open")]]
                ),
                parse_mode=None,
            )
        elif command.startswith("confirm:"):
            data = await state.get_data()
            if (
                await state.get_state() != PersonalInput.confirmation.state
                or data.get("token") != command[8:]
                or time.monotonic() - data.get("at", 0) > 300
            ):
                await message.answer(
                    "Подтверждение устарело. Открой «Обо мне» заново.", parse_mode=None
                )
                return
            if data["kind"] == "delete":
                await personal_profiles.clear(actor)
            else:
                await personal_profiles.save(actor, data["summary"])
            await state.clear()
            await show(message, actor, personal_profiles)
        else:
            await message.answer("Действие устарело. Открой «Обо мне» заново.", parse_mode=None)
    except Exception:
        logger.warning("Personal profile action failed; no content logged.")
        await message.answer("Не удалось обновить сведения. Попробуй позже.", parse_mode=None)


async def description(message: Message, state: FSMContext, personal_profiles: PersonalProfiles):
    if message.from_user is None:
        return
    text = (message.text or "").strip()
    if not 1 <= len(text) <= MAX_INPUT:
        await message.answer(
            f"Нужно от 1 до {MAX_INPUT} символов. Повтори ввод или /cancel.", parse_mode=None
        )
        return
    await state.set_state(PersonalInput.generating)
    token = uuid.uuid4().hex
    await state.set_data({"generation": token})
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            summary = await personal_profiles.compact(message.from_user.id, text)
        if (
            await state.get_state() != PersonalInput.generating.state
            or (await state.get_data()).get("generation") != token
        ):
            return
        await state.set_state(PersonalInput.confirmation)
        await state.set_data(
            {"token": token, "kind": "save", "summary": summary, "at": time.monotonic()}
        )
        await message.answer(
            "Предлагаемая сводка:\n\n"
            + summary
            + "\n\nПроверь точность: модель могла пропустить важное. "
            "Сохранять только после подтверждения; кнопка действует 5 минут.",
            reply_markup=keyboard([[("Сохранить", f"pm:confirm:{token}"), ("Отмена", "pm:open")]]),
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception:
        if (await state.get_data()).get("generation") == token:
            await state.set_state(PersonalInput.description)
        logger.warning("Personal profile summary failed; no content logged.")
        await message.answer(
            "Не удалось подготовить сводку. Прежний профиль сохранён. "
            "Попробуй уточнить описание или /cancel; запрос мог быть оплачен.",
            parse_mode=None,
        )


async def generating(message: Message):
    await message.answer(
        "Сводка ещё готовится. Дождись результата или отправь /cancel.", parse_mode=None
    )


def create_router():
    router = Router(name="personal_profile")
    router.callback_query.register(action, F.data.startswith("pm:"))
    router.message.filter(F.chat.type == "private", F.text)
    router.message.register(
        description, PersonalInput.description, ~F.text.startswith("/"), ~F.text.in_(BUTTONS)
    )
    router.message.register(
        generating, PersonalInput.generating, ~F.text.startswith("/"), ~F.text.in_(BUTTONS)
    )
    return router
