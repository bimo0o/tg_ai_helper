from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.assistant import ERROR_MESSAGE
from app.config import Settings
from app.operations import OperationsRepository
from app.prompts import MODES
from app.repository import BUSY_MESSAGE, DialogueBusy, Repository
from app.ui import WELCOME, main_keyboard

START_MESSAGE = WELCOME


async def start(
    message: Message,
    operations: OperationsRepository | None = None,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.clear()
    owner = bool(
        operations is not None
        and message.from_user is not None
        and await operations.is_admin(message.from_user.id)
    )
    await message.answer(START_MESSAGE, reply_markup=main_keyboard(owner), parse_mode=None)


async def change_mode(
    message: Message,
    command: CommandObject,
    repository: Repository,
    state: FSMContext | None = None,
) -> None:
    if message.from_user is None:
        return
    if state is not None:
        await state.clear()
    mode = command.command
    try:
        await repository.set_mode((message.chat.id, message.from_user.id), mode)
        text = f"Включён режим «{MODES[mode]}». История очищена."
    except DialogueBusy:
        text = BUSY_MESSAGE
    except Exception:
        text = ERROR_MESSAGE
    await message.answer(text, parse_mode=None)


async def show_settings(
    message: Message,
    command: CommandObject,
    repository: Repository,
    settings: Settings,
    operations: OperationsRepository | None = None,
    state: FSMContext | None = None,
) -> None:
    if message.from_user is None:
        return
    if state is not None:
        await state.clear()
    argument = command.args.strip() if command.args is not None else None
    if argument is not None and argument not in {"0.0", "0.3", "0.7", "1.0"}:
        await message.answer(
            "Допустимые значения temperature: 0.0, 0.3, 0.7, 1.0. Пример: /settings 0.3",
            parse_mode=None,
        )
        return
    owner = (message.chat.id, message.from_user.id)
    try:
        async with repository.locked(owner) as locked:
            if argument is not None:
                await locked.set_temperature(owner, float(argument))
            user = await locked.get_settings(owner)
        model = await operations.get_model(settings.llm_model) if operations else settings.llm_model
        text = (
            f"Режим: /{user.mode} ({MODES[user.mode]})\n"
            f"Модель: {model}\n"
            f"Temperature: {user.temperature:.1f}\n"
            "Изменить: /settings 0.0|0.3|0.7|1.0"
        )
    except DialogueBusy:
        text = BUSY_MESSAGE
    except Exception:
        text = ERROR_MESSAGE
    await message.answer(text, parse_mode=None)


async def reset(message: Message, repository: Repository, state: FSMContext | None = None) -> None:
    if message.from_user is None:
        return
    if state is not None:
        await state.clear()
    owner = (message.chat.id, message.from_user.id)
    try:
        async with repository.locked(owner) as locked:
            await locked.clear_history(owner)
        text = "История очищена. Режим и temperature сохранены."
    except DialogueBusy:
        text = BUSY_MESSAGE
    except Exception:
        text = ERROR_MESSAGE
    await message.answer(text, parse_mode=None)


def create_router() -> Router:
    router = Router(name="commands")
    router.message.filter(F.chat.type == "private", F.text)
    router.message.register(start, CommandStart())
    router.message.register(start, Command("menu"))
    router.message.register(change_mode, Command("study", "translate", "music", "chat", "web"))
    router.message.register(show_settings, Command("settings"))
    router.message.register(reset, Command("reset"))
    return router
