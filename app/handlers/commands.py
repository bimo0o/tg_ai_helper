from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from app.assistant import ERROR_MESSAGE
from app.config import Settings
from app.prompts import MODES
from app.repository import Repository

START_MESSAGE = (
    "Я — AI-ассистент студента. Помогу с программированием, переводом и созданием музыки.\n"
    "/study — учебные вопросы по программированию\n"
    "/translate — перевод (укажите целевой язык в сообщении)\n"
    "/music — создание музыки, сведение и плагины; используется платный web-поиск\n"
    "/settings — режим, модель и temperature\n"
    "/settings 0.0|0.3|0.7|1.0 — изменить temperature\n"
    "/reset — очистить историю, сохранив настройки\n"
    "По умолчанию включён /study. Отправьте текстовый вопрос."
)


async def start(message: Message) -> None:
    await message.answer(START_MESSAGE, parse_mode=None)


async def change_mode(message: Message, command: CommandObject, repository: Repository) -> None:
    if message.from_user is None:
        return
    mode = command.command
    try:
        await repository.set_mode((message.chat.id, message.from_user.id), mode)
        text = f"Включён режим «{MODES[mode]}». История очищена."
    except Exception:
        text = ERROR_MESSAGE
    await message.answer(text, parse_mode=None)


async def show_settings(
    message: Message, command: CommandObject, repository: Repository, settings: Settings
) -> None:
    if message.from_user is None:
        return
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
        text = (
            f"Режим: /{user.mode} ({MODES[user.mode]})\n"
            f"Модель: {settings.llm_model}\n"
            f"Temperature: {user.temperature:.1f}\n"
            "Изменить: /settings 0.0|0.3|0.7|1.0"
        )
    except Exception:
        text = ERROR_MESSAGE
    await message.answer(text, parse_mode=None)


def create_router() -> Router:
    router = Router(name="commands")
    router.message.filter(F.chat.type == "private", F.text)
    router.message.register(start, CommandStart())
    router.message.register(change_mode, Command("study", "translate", "music"))
    router.message.register(show_settings, Command("settings"))
    return router
