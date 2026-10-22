"""Durable delivery without LLM calls. Unknown acceptance is never retried automatically."""

import asyncio
import logging
from datetime import timedelta

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import LinkPreviewOptions

from app.reminder_time import timezone

logger = logging.getLogger(__name__)


def delivery_text(row, now):
    due = row["remind_at"].astimezone(timezone(row["timezone"]))
    late = now - row["remind_at"] > timedelta(minutes=1)
    return (
        "⏰ Напоминание"
        + (" · доставлено с задержкой" if late else "")
        + f"\nНазначено: {due:%Y-%m-%d %H:%M} ({row['timezone']})\n\n{row['text']}"
    )


async def send(bot, row, now):
    for attempt in range(2):
        try:
            async with asyncio.timeout(35):
                await bot.send_message(
                    row["chat_id"],
                    delivery_text(row, now),
                    parse_mode=None,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            return "sent"
        except TelegramForbiddenError:
            return "blocked"
        except TelegramRetryAfter as error:
            # Telegram explicitly rejected this attempt; only this failure is safe to retry.
            if attempt == 0 and 0 <= error.retry_after <= 10:
                await asyncio.sleep(error.retry_after)
                continue
            return "unconfirmed"
        except asyncio.CancelledError:
            raise
        except Exception:
            return "unconfirmed"
    return "unconfirmed"


async def deliver_one(reminders, bot) -> bool:
    row = await reminders.claim()
    if row is None:
        return False
    status = "unconfirmed"
    try:
        status = await send(bot, row, reminders.clock())
    finally:
        # Cancellation during send is ambiguous too. The DB stays open until worker shutdown.
        await asyncio.shield(reminders.finish(row["id"], row["claim_token"], status))
    return True


async def delivery_worker(reminders, bot):
    while True:
        try:
            if await deliver_one(reminders, bot):
                continue
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Reminder delivery failed kind=%s", type(error).__name__)
        await asyncio.sleep(5)
