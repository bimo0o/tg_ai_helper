import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.reminder_delivery import deliver_one, send


def reminder():
    now = datetime(2026, 10, 22, tzinfo=UTC)
    row = {
        "id": uuid4(),
        "claim_token": uuid4(),
        "chat_id": 1,
        "timezone": "UTC",
        "remind_at": now - timedelta(minutes=2),
        "text": "<b>Synthetic</b>",
    }
    return now, row


async def test_delivery_preserves_literal_text_original_date_and_delay_notice():
    now, row = reminder()
    repository = AsyncMock()
    repository.claim.return_value = row
    repository.clock = lambda: now
    bot = AsyncMock()
    assert await deliver_one(repository, bot)
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["parse_mode"] is None
    assert "с задержкой" in bot.send_message.call_args.args[1]
    assert "<b>Synthetic</b>" in bot.send_message.call_args.args[1]
    repository.finish.assert_awaited_once_with(row["id"], row["claim_token"], "sent")


async def test_unknown_acceptance_is_marked_unconfirmed_without_retry():
    now, row = reminder()
    bot = AsyncMock()
    bot.send_message.side_effect = TimeoutError("private details")
    assert await send(bot, row, now) == "unconfirmed"
    assert bot.send_message.await_count == 1


async def test_explicit_telegram_rejection_allows_one_bounded_retry():
    now, row = reminder()
    method = SendMessage(chat_id=1, text="Synthetic")
    bot = AsyncMock()
    bot.send_message.side_effect = [
        TelegramRetryAfter(method=method, message="Retry", retry_after=0),
        None,
    ]
    assert await send(bot, row, now) == "sent"
    assert bot.send_message.await_count == 2
    bot.send_message.reset_mock()
    bot.send_message.side_effect = TelegramRetryAfter(
        method=method, message="Retry", retry_after=60
    )
    assert await send(bot, row, now) == "unconfirmed"
    assert bot.send_message.await_count == 1
    bot.send_message.side_effect = TelegramForbiddenError(method=method, message="Forbidden")
    assert await send(bot, row, now) == "blocked"


async def test_cancellation_during_send_finishes_ambiguously_before_shutdown():
    now, row = reminder()
    repository = AsyncMock()
    repository.claim.return_value = row
    repository.clock = lambda: now
    entered = asyncio.Event()

    async def pending(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    bot = AsyncMock()
    bot.send_message.side_effect = pending
    task = asyncio.create_task(deliver_one(repository, bot))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    repository.finish.assert_awaited_once_with(row["id"], row["claim_token"], "unconfirmed")


async def test_no_due_claim_does_not_call_telegram():
    repository = AsyncMock()
    repository.claim.return_value = None
    bot = AsyncMock()
    assert not await deliver_one(repository, bot)
    bot.send_message.assert_not_awaited()
