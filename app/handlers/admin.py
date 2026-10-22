import logging
import uuid

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.admin import DENIED, AdminService, AdminView

logger = logging.getLogger(__name__)
ADMIN_ERROR = "Не удалось обновить админ-панель. Попробуйте открыть /admin заново."


def log_failure(stage, error):
    request_id = uuid.uuid4().hex
    logger.warning(
        "Admin panel failed request_id=%s stage=%s error_type=%s",
        request_id,
        stage,
        type(error).__name__,
    )


async def update_view(message, view):
    markup = keyboard(view)
    if message.text == view.text and message.reply_markup == markup:
        return
    try:
        await message.edit_text(view.text, reply_markup=markup, parse_mode=None)
    except TelegramBadRequest as error:
        reason = error.message.lower()
        if "message is not modified" in reason:
            return
        if "message can't be edited" in reason or "message to edit not found" in reason:
            await message.answer(view.text, reply_markup=markup, parse_mode=None)
            return
        raise


def keyboard(view: AdminView) -> InlineKeyboardMarkup:
    def button(text, data):
        return InlineKeyboardButton(text=text, callback_data=data)

    rows = [
        [
            button("24 часа", "adm:view:day:0"),
            button("7 дней", "adm:view:week:0"),
            button("Всё время", "adm:view:all:0"),
        ],
        [
            button("Выбрать Qwen", "adm:model:qwen"),
            button("DeepSeek V4.1 Flash", "adm:model:deepseek"),
        ],
    ]
    navigation = []
    if view.offset:
        navigation.append(button("Назад", f"adm:view:{view.period}:{view.offset - 10}"))
    if view.has_next:
        navigation.append(button("Далее", f"adm:view:{view.period}:{view.offset + 10}"))
    if navigation:
        rows.append(navigation)
    rows.append([button("Обновить", f"adm:view:{view.period}:{view.offset}")])
    rows.append([button("Тест температур", "temp:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def panel(message: Message, admin: AdminService, state: FSMContext | None = None) -> None:
    if message.from_user is None:
        return
    if state is not None:
        await state.clear()
    try:
        view = await admin.overview(message.from_user.id)
        if view is None:
            await message.answer(DENIED, parse_mode=None)
        else:
            await message.answer(view.text, reply_markup=keyboard(view), parse_mode=None)
    except Exception as error:
        log_failure("open", error)
        await message.answer(ADMIN_ERROR, parse_mode=None)


async def action(
    callback: CallbackQuery, admin: AdminService, state: FSMContext | None = None
) -> None:
    message = callback.message
    if (
        not isinstance(message, Message)
        or message.chat.type != "private"
        or message.chat.id != callback.from_user.id
    ):
        await callback.answer(DENIED, show_alert=True)
        return
    stage = "authorize"
    try:
        if not await admin.authorized(callback.from_user.id):
            await callback.answer(DENIED, show_alert=True)
            return
        if state is not None:
            await state.clear()
        stage = "acknowledge"
        try:
            await callback.answer()
        except TelegramBadRequest as error:
            reason = error.message.lower()
            if "query is too old" not in reason and "query id is invalid" not in reason:
                raise
        stage = "overview"
        parts = (callback.data or "").split(":")
        if len(parts) == 3 and parts[:2] == ["adm", "model"]:
            if not await admin.switch_model(callback.from_user.id, parts[2]):
                await message.answer(DENIED, parse_mode=None)
                return
            view = await admin.overview(callback.from_user.id)
        elif len(parts) == 4 and parts[:2] == ["adm", "view"]:
            view = await admin.overview(callback.from_user.id, parts[2], int(parts[3]))
        else:
            raise ValueError("Invalid action.")
        if view is None:
            await message.answer(DENIED, parse_mode=None)
            return
        stage = "edit"
        await update_view(message, view)
    except ValueError:
        await message.answer("Некорректное действие. Откройте /admin заново.", parse_mode=None)
    except Exception as error:
        log_failure(stage, error)
        await message.answer(ADMIN_ERROR, parse_mode=None)


def create_router() -> Router:
    router = Router(name="admin")
    router.message.register(panel, Command("admin"), F.chat.type == "private")
    router.callback_query.register(action, F.data.startswith("adm:"))
    return router
