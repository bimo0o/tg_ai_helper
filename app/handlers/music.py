from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.assistant import ERROR_MESSAGE, Assistant
from app.music_profile import FIELDS, MusicProfiles
from app.rendering import answer_formatted
from app.text import split_text

PROFILE_HELP = (
    "Музыкальный профиль\n"
    "/profile — посмотреть\n"
    "/profile <поле> <значение> — сохранить (до 240 символов)\n"
    "Поля: daw, os, genres, plugins, equipment, budget, level\n"
    "Пример: /profile daw Ableton Live\n"
    "/profile clear — удалить профиль. /reset очищает только историю."
)


async def profile(
    message: Message,
    command: CommandObject,
    profiles: MusicProfiles,
    state: FSMContext | None = None,
) -> None:
    if message.from_user is None:
        return
    if state is not None:
        await state.clear()
    argument = (command.args or "").strip()
    try:
        if argument == "clear":
            await profiles.clear(message.from_user.id)
            text = "Музыкальный профиль удалён. История диалога сохранена."
        elif argument:
            parts = argument.split(maxsplit=1)
            if len(parts) != 2:
                raise ValueError
            await profiles.set_field(message.from_user.id, parts[0], parts[1])
            text = f"Сохранено поле «{FIELDS[parts[0]]}»."
        else:
            data = await profiles.get(message.from_user.id)
            values = "\n".join(
                f"{label}: {data[key]}" for key, label in FIELDS.items() if key in data
            )
            text = PROFILE_HELP + ("\n\n" + values if values else "\n\nПрофиль пока пуст.")
    except ValueError:
        text = PROFILE_HELP
    except Exception:
        text = ERROR_MESSAGE
    for part in split_text(text):
        await message.answer(part, parse_mode=None)


async def search(
    message: Message,
    command: CommandObject,
    assistant: Assistant,
    state: FSMContext | None = None,
) -> None:
    if message.from_user is None:
        return
    if state is not None:
        await state.clear()
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "Используйте /search <вопрос на любую тему> или включите режим /web.", parse_mode=None
        )
        return
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            reply = await assistant.respond(
                (message.chat.id, message.from_user.id), query, use_web=True
            )
        text = reply.text
    except Exception:
        text = ERROR_MESSAGE
    await answer_formatted(message, text or ERROR_MESSAGE)


def create_router() -> Router:
    router = Router(name="music")
    router.message.filter(F.chat.type == "private", F.text)
    router.message.register(profile, Command("profile"))
    router.message.register(search, Command("search"))
    return router
