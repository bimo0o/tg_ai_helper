from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.admin import DENIED, AdminService, AdminView
from app.assistant import ERROR_MESSAGE


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
    except Exception:
        await message.answer(ERROR_MESSAGE, parse_mode=None)


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
    try:
        if not await admin.authorized(callback.from_user.id):
            await callback.answer(DENIED, show_alert=True)
            return
        if state is not None:
            await state.clear()
        await callback.answer()
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
        markup = keyboard(view)
        if message.text != view.text or message.reply_markup != markup:
            await message.edit_text(view.text, reply_markup=markup, parse_mode=None)
    except ValueError:
        await message.answer("Некорректное действие. Откройте /admin заново.", parse_mode=None)
    except Exception:
        await message.answer(ERROR_MESSAGE, parse_mode=None)


def create_router() -> Router:
    router = Router(name="admin")
    router.message.register(panel, Command("admin"), F.chat.type == "private")
    router.callback_query.register(action, F.data.startswith("adm:"))
    return router
