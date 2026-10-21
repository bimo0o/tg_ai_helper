import logging

from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.assistant import ERROR_MESSAGE, Assistant
from app.rendering import answer_formatted

logger = logging.getLogger(__name__)
UNKNOWN_COMMAND = "Неизвестная команда. Список команд: /start"


async def reply_to_text(message: Message, assistant: Assistant) -> None:
    if message.from_user is None or message.text is None:
        return
    if message.text.startswith("/"):
        await message.answer(UNKNOWN_COMMAND, parse_mode=None)
        return
    owner = (message.chat.id, message.from_user.id)
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            result = await assistant.respond(owner, message.text)
        text = result.text if result.text.strip() else ERROR_MESSAGE
    except Exception:
        logger.warning("Message processing failed; no request content logged.")
        text = ERROR_MESSAGE
    await answer_formatted(message, text)


def create_router() -> Router:
    router = Router(name="messages")
    router.message.filter(F.chat.type == "private", F.text)
    router.message.register(reply_to_text)
    return router
