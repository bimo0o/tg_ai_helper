"""Reminder UI adapters. Confirmation is deterministic and persisted independently of FSM."""

import json
import logging
import time
from datetime import datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.mcp.client import ToolUnavailable
from app.reminder_time import ReminderInputError, local_instant, reminder_instant, timezone
from app.reminders import ReminderUnavailable
from app.ui import BUTTONS, REMINDERS

logger = logging.getLogger(__name__)


class Input(StatesGroup):
    zone = State()
    text = State()
    date = State()


def keyboard(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=key) for text, key in row]
            for row in rows
        ]
    )


def confirmation_keyboard(action_id):
    return keyboard(
        [[("Подтвердить", f"rm:confirm:{action_id}"), ("Отмена", f"rm:dismiss:{action_id}")]]
    )


async def show_proposal(message, action):
    local = action["remind_at"].astimezone(timezone(action["timezone"]))
    await message.answer(
        f"Создать напоминание?\n{local:%Y-%m-%d %H:%M} ({action['timezone']})\n\n"
        + action["text"]
        + "\n\nПодтверждение действует 5 минут. Пока ничего не запланировано.",
        reply_markup=confirmation_keyboard(action["id"]),
        parse_mode=None,
    )


async def show_list(message, actor, reminders, page=0):
    rows = await reminders.list((message.chat.id, actor))
    zone = await reminders.get_timezone(actor)
    page = max(0, min(page, max(0, (len(rows) - 1) // 5)))
    selected = rows[page * 5 : page * 5 + 5]
    lines = ["⏰ Напоминания", f"Часовой пояс: {zone or 'не задан'}"]
    buttons = [[("Создать", "rm:new"), ("Часовой пояс", "rm:zone")]]
    for index, row in enumerate(selected, page * 5 + 1):
        local = row["remind_at"].astimezone(timezone(row["timezone"]))
        status = {
            "scheduled": "ожидает",
            "sending": "отправляется",
            "unconfirmed": "доставка не подтверждена",
        }[row["status"]]
        lines.append(
            f"\n{index}. {local:%Y-%m-%d %H:%M} ({row['timezone']}) · {status}\n"
            + row["text"][:180]
        )
        if row["status"] == "scheduled":
            buttons.append([(f"Отменить № {index}", f"rm:cancel:{row['id']}")])
    if not rows:
        lines.append("Активных напоминаний нет.")
    navigation = []
    if page:
        navigation.append(("←", f"rm:list:{page - 1}"))
    if len(rows) > (page + 1) * 5:
        navigation.append(("→", f"rm:list:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append([("Обновить", f"rm:list:{page}"), ("В меню", "ui:home")])
    await message.answer("\n".join(lines), reply_markup=keyboard(buttons), parse_mode=None)


async def open_panel(message: Message, state: FSMContext, reminders):
    if message.from_user:
        await state.clear()
        await show_list(message, message.from_user.id, reminders)


async def timezone_command(message: Message, command: CommandObject, reminders):
    if not message.from_user:
        return
    if command.args:
        try:
            await reminders.set_timezone(message.from_user.id, command.args.strip())
        except ReminderInputError as error:
            await message.answer(str(error), parse_mode=None)
            return
    zone = await reminders.get_timezone(message.from_user.id)
    text = "Часовой пояс не задан. Пример: /timezone Europe/Moscow"
    if zone:
        local = reminders.clock().astimezone(timezone(zone))
        text = f"Часовой пояс: {zone}\nСейчас: {local:%Y-%m-%d %H:%M}"
    await message.answer(text, parse_mode=None)


async def action(callback: CallbackQuery, state: FSMContext, reminders, tools=None):
    message, actor = callback.message, callback.from_user.id
    if (
        not isinstance(message, Message)
        or message.chat.type != "private"
        or message.chat.id != actor
    ):
        await callback.answer("Недоступно.", show_alert=True)
        return
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass  # An expired callback acknowledgment must not prevent persisted confirmation.
    owner = (message.chat.id, actor)
    command = (callback.data or "")[3:]
    try:
        if command.startswith("list:"):
            await state.clear()
            await show_list(message, actor, reminders, int(command[5:]))
        elif command == "zone":
            await state.clear()
            await state.set_state(Input.zone)
            await message.answer(
                "Укажи пояс IANA, например Europe/Moscow. Выход: /cancel.", parse_mode=None
            )
        elif command == "new":
            await state.clear()
            if not await reminders.get_timezone(actor):
                await message.answer(
                    "Сначала задай часовой пояс кнопкой ниже.",
                    reply_markup=keyboard([[("Часовой пояс", "rm:zone")]]),
                    parse_mode=None,
                )
                return
            await state.set_state(Input.text)
            await message.answer(
                "О чём напомнить? От 1 до 500 символов. Выход: /cancel.", parse_mode=None
            )
        elif command.startswith("dismiss:"):
            changed = await reminders.dismiss(owner, UUID(command[8:]))
            await message.answer(
                "Создание отменено." if changed else "Заявка уже закрыта или недоступна.",
                parse_mode=None,
            )
        elif command.startswith("cancel:"):
            changed = await reminders.cancel(owner, UUID(command[7:]))
            await message.answer(
                "Напоминание отменено."
                if changed
                else "Отмена недоступна: запись закрыта, чужая или уже отправляется.",
                parse_mode=None,
            )
        elif command.startswith("confirm:"):
            if tools is None:
                raise ToolUnavailable
            proposal = await reminders.confirm(owner, UUID(command[8:]))
            started = time.monotonic()
            result = await tools.call(
                "add_reminder",
                {"text": proposal["text"], "remind_at": proposal["remind_at"].isoformat()},
                owner=owner,
                action_id=proposal["id"],
            )
            try:
                await reminders.audit(
                    owner, "add_reminder", "success", int((time.monotonic() - started) * 1000)
                )
                if "source_key" in proposal:
                    await reminders.decision(
                        owner,
                        proposal["source_key"],
                        "tool",
                        "add_reminder",
                        {"remind_at": proposal["remind_at"].isoformat()},
                        "success",
                    )
            except Exception:
                logger.warning("Reminder audit incomplete; no arguments logged")
            await message.answer(
                "Напоминание создано."
                if result["created"]
                else "Это напоминание уже было создано; дубликат не добавлен.",
                parse_mode=None,
            )
    except ReminderUnavailable:
        await message.answer(
            "Подтверждение истекло, отменено или недоступно. Создай новую заявку.", parse_mode=None
        )
    except ToolUnavailable:
        await message.answer(
            "MCP сейчас недоступен. Создание не подтверждено. "
            "Можно повторить кнопку до истечения 5 минут.",
            parse_mode=None,
        )
    except (ValueError, KeyError):
        await message.answer("Некорректная или устаревшая кнопка.", parse_mode=None)
    except Exception as error:
        logger.warning("Reminder UI failed kind=%s", type(error).__name__)
        await message.answer("Не удалось выполнить действие. Попробуй позже.", parse_mode=None)


async def input_value(message: Message, state: FSMContext, reminders):
    if not message.from_user or not message.text:
        return
    actor, value = message.from_user.id, message.text.strip()
    current = await state.get_state()
    try:
        if current == Input.zone.state:
            await reminders.set_timezone(actor, value)
            await state.clear()
            await timezone_command(message, CommandObject(command="timezone"), reminders)
        elif current == Input.text.state:
            if not 1 <= len(value) <= 500:
                raise ReminderInputError("Нужно от 1 до 500 символов.")
            await state.update_data(text=value)
            await state.set_state(Input.date)
            zone = await reminders.get_timezone(actor)
            await message.answer(
                f"Дата и время в {zone}: ГГГГ-ММ-ДД ЧЧ:ММ. "
                "Можно ISO 8601 со смещением UTC. Выход: /cancel.",
                parse_mode=None,
            )
        elif current == Input.date.state:
            zone = await reminders.get_timezone(actor)
            if not zone:
                raise ReminderInputError("Сначала укажи часовой пояс через /timezone.")
            parsed = datetime.fromisoformat(value)
            instant = (
                reminder_instant(value, zone, reminders.clock())
                if parsed.tzinfo
                else local_instant(parsed, zone)
            )
            data = await state.get_data()
            proposal = await reminders.propose(
                (message.chat.id, actor),
                f"telegram:{message.message_id}",
                data["text"],
                instant.astimezone(timezone(zone)).isoformat(),
                zone,
            )
            await state.clear()
            await show_proposal(message, proposal)
    except ReminderInputError as error:
        await message.answer(str(error), parse_mode=None)
    except ValueError:
        await message.answer(
            "Не удалось разобрать дату. Пример: 2030-01-10 15:30.", parse_mode=None
        )


def create_router():
    router = Router(name="reminders")
    router.message.filter(F.chat.type == "private")
    router.message.register(open_panel, Command("reminders"))
    router.message.register(open_panel, F.text == REMINDERS)
    router.message.register(timezone_command, Command("timezone"))
    router.message.register(tools_command, Command("tools"))
    router.message.register(why_command, Command("why"))
    for step in (Input.zone, Input.text, Input.date):
        router.message.register(
            input_value, step, F.text, ~F.text.startswith("/"), ~F.text.in_(BUTTONS)
        )
    router.callback_query.register(action, F.data.startswith("rm:"))
    return router


async def tools_command(message: Message, tools=None):
    try:
        if tools is None:
            raise ToolUnavailable
        listing = await tools.discover()
        lines = ["Инструменты, обнаруженные через MCP:"]
        for tool in listing:
            kind = "запись · требует подтверждения" if tool.name == "add_reminder" else "чтение"
            lines.append(f"• {tool.name} — {tool.description} ({kind})")
        await message.answer("\n".join(lines), parse_mode=None)
    except Exception:
        await message.answer(
            "MCP-инструменты сейчас недоступны. Обычный диалог работает.", parse_mode=None
        )


async def why_command(message: Message, reminders):
    if not message.from_user:
        return
    owner = (message.chat.id, message.from_user.id)
    row = await reminders.last_decision(owner)
    if row:
        arguments = row["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        zone = await reminders.get_timezone(message.from_user.id) or "UTC"
        stamp = row["created_at"].astimezone(timezone(zone))
        actions = {
            "answer": "ответ без инструмента",
            "clarification": "уточнение",
            "tool": "инструмент",
            "limit": "лимит",
            "error": "ошибка",
        }
        reasons = {
            "get_weather": "нужны актуальные погодные данные",
            "get_schedule": "нужно настроенное расписание",
            "add_reminder": "нужно явное подтверждение записи",
        }
        text = (
            f"Последний запрос: {stamp:%Y-%m-%d %H:%M} ({zone})\n"
            f"Действие: {actions[row['action']]} {row['tool'] or ''}\n"
            f"Безопасные аргументы: {json.dumps(arguments, ensure_ascii=False)}\n"
            f"Проверка/выполнение: {row['status']}\nПричина: "
            + reasons.get(
                row["tool"], "выбрано нативным маршрутизатором; скрытые рассуждения не сохраняются"
            )
        )
        await message.answer(text, parse_mode=None)
        return
    row = await reminders.last_audit(owner)
    text = "Вызовов инструментов пока нет."
    if row:
        text = (
            f"Последнее действие: {row['tool']}\nРезультат: {row['outcome']}\n"
            f"Время выполнения: {row['duration_ms']} мс\n"
            "Это журнал действий, без внутренних рассуждений модели и текстов запросов."
        )
    await message.answer(text, parse_mode=None)
