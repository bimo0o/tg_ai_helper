"""Log only structural metadata for updates that no handler accepted."""

import logging

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Update

logger = logging.getLogger(__name__)


class UpdateDiagnostics(BaseMiddleware):
    async def __call__(self, handler, event: Update, data):
        result = await handler(event, data)
        if result is UNHANDLED:
            try:
                event_type = event.event_type
                payload = event.event
                message = payload if hasattr(payload, "chat") else getattr(payload, "message", None)
                chat_type = getattr(getattr(message, "chat", None), "type", "none")
                content_type = getattr(message, "content_type", "none")
                content_type = getattr(content_type, "value", content_type)
                text = getattr(message, "text", None)
                caption = getattr(message, "caption", None)
                logger.warning(
                    "Unhandled update update_id=%d event_type=%s chat_type=%s "
                    "content_type=%s text_chars=%d caption_chars=%d",
                    event.update_id,
                    event_type,
                    chat_type,
                    content_type,
                    len(text) if isinstance(text, str) else 0,
                    len(caption) if isinstance(caption, str) else 0,
                )
            except Exception:
                logger.warning("Unhandled update metadata unavailable")
        return result
