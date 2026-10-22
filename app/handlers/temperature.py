import logging
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.admin import DENIED
from app.temperature import LAB_PROMPT, TemperatureService, export_html, export_json, summary
from app.text import split_text
from app.ui import BUTTONS

logger = logging.getLogger(__name__)


class TemperatureInput(StatesGroup):
    prompt = State()


def keyboard(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def run_keyboard(run_id, status):
    rows = []
    if status == "draft":
        rows.append([("Запустить 12 платных запросов", f"temp:start:{run_id}")])
    if status in {"draft", "running"}:
        rows.append([("Отменить тест", f"temp:cancel:{run_id}")])
    rows.append([("Скачать отчёт", f"temp:export:{run_id}")])
    rows.append([("К тестам температур", "temp:open")])
    return keyboard(rows)


async def show_run(message, actor, service, run_id):
    experiment = await service.get(actor, run_id)
    if experiment is None:
        await message.answer("Тест не найден или истёк срок хранения.", parse_mode=None)
        return
    if experiment["status"] == "draft":
        data = experiment["data"]
        for part in split_text("Запрос для сравнения:\n\n" + data["prompt"]):
            await message.answer(part, parse_mode=None)
        text = (
            f"Модель: {data['model']}\nРежим: {data['mode']}\n"
            f"Ответ до {data['max_output_tokens']} токенов; "
            f"таймаут каждого запроса {data['timeout_seconds']} с.\n\n"
            "Будет 12 платных запросов: 0 / 0.3 / 0.7 / 1.0 × 3. "
            "История пуста перед каждым вызовом, web и музыкальный профиль выключены. "
            "Ваш диалог и настройки не изменятся. Модель зафиксирована для всего теста.\n\n"
            "Разные ответы не гарантированы. Ошибки и обрезанные ответы тоже сохраняются. "
            "Подтверждение запуска действует 15 минут; результаты хранятся 30 дней."
        )
    else:
        text = summary(experiment)
    await message.answer(
        text, reply_markup=run_keyboard(run_id, experiment["status"]), parse_mode=None
    )


async def action(callback: CallbackQuery, state: FSMContext, temperature: TemperatureService):
    message = callback.message
    actor = callback.from_user.id
    if (
        not isinstance(message, Message)
        or message.chat.type != "private"
        or message.chat.id != actor
    ):
        await callback.answer(DENIED, show_alert=True)
        return
    try:
        if not await temperature.authorized(actor):
            await callback.answer(DENIED, show_alert=True)
            return
        await callback.answer()
        await state.clear()
        parts = (callback.data or "").split(":")
        command = parts[1]
        if command == "open" and len(parts) == 2:
            await message.answer(
                "Сравнение температур\n\nОдин запрос, одна модель, четыре температуры "
                "по три повтора. Лабораторный запрос использует режим «Учёба», "
                "свой запрос — «Диалог». Каждый запуск требует отдельного подтверждения. "
                "Тест помогает сравнить ответы, но не доказывает, что высокая температура "
                "улучшает качество.",
                reply_markup=keyboard(
                    [
                        [("Запрос для лабораторной", "temp:preset")],
                        [("Свой запрос", "temp:custom")],
                        [("Последний тест", "temp:last")],
                    ]
                ),
                parse_mode=None,
            )
        elif command == "preset" and len(parts) == 2:
            run_id = await temperature.prepare(actor, LAB_PROMPT, "study")
            await show_run(message, actor, temperature, run_id)
        elif command == "custom" and len(parts) == 2:
            await state.set_state(TemperatureInput.prompt)
            await message.answer(
                "Отправьте один запрос для всех 12 ответов. Лучше выбрать объяснение "
                "или творческую задачу без актуальных данных. Следующее сообщение "
                "только подготовит тест; запуск подтвердите отдельно. "
                "Выход: /cancel или кнопка «Меню».",
                parse_mode=None,
            )
        elif command == "last" and len(parts) == 2:
            last = await temperature.get(actor)
            if last is None:
                await message.answer("Сохранённых тестов нет.", parse_mode=None)
            else:
                await show_run(message, actor, temperature, last["id"])
        elif len(parts) == 3 and command in {"start", "cancel", "export"}:
            run_id = uuid.UUID(parts[2])
            if command == "cancel":
                await temperature.cancel(actor, run_id)
                await message.answer(
                    "Отмена запрошена. Уже отправленный запрос может завершиться и быть "
                    "оплачен; следующие вызовы будут остановлены.",
                    parse_mode=None,
                )
                await show_run(message, actor, temperature, run_id)
            elif command == "export":
                experiment = await temperature.get(actor, run_id)
                if experiment is None:
                    raise ValueError
                for name, content in (
                    ("temperature-report.html", export_html(experiment)),
                    ("temperature-results.json", export_json(experiment)),
                ):
                    await message.answer_document(BufferedInputFile(content, filename=name))
            else:
                notice = await message.answer(
                    "Проверяю запуск теста. Отчёт будет сохранён после каждого ответа.",
                    parse_mode=None,
                    reply_markup=run_keyboard(run_id, "running"),
                )

                async def progress(done):
                    await notice.edit_text(
                        f"Тест температур: сохранено {done}/12 результатов. "
                        "Можно отменить оставшиеся вызовы.",
                        parse_mode=None,
                        reply_markup=run_keyboard(run_id, "running"),
                    )

                started = await temperature.run(actor, run_id, progress)
                if not started:
                    await message.answer(
                        "Запуск отклонён: тест уже запускался, другой тест выполняется "
                        "или подтверждение истекло. При изменении настроек сервера "
                        "подготовьте тест заново.",
                        parse_mode=None,
                    )
                await show_run(message, actor, temperature, run_id)
        else:
            raise ValueError
    except PermissionError:
        await message.answer(DENIED, parse_mode=None)
    except ValueError:
        await message.answer(
            "Некорректное действие. Откройте тест из /admin заново.", parse_mode=None
        )
    except Exception:
        logger.warning("Temperature action failed; no user data logged.")
        await message.answer(
            "Тест не удалось завершить. Сохранённую часть можно открыть через "
            "«Последний тест». Платные вызовы автоматически не повторяются.",
            parse_mode=None,
        )


async def custom_prompt(message: Message, state: FSMContext, temperature: TemperatureService):
    if message.from_user is None:
        return
    try:
        run_id = await temperature.prepare(message.from_user.id, message.text or "", "chat")
    except PermissionError:
        await state.clear()
        await message.answer(DENIED, parse_mode=None)
        return
    except ValueError:
        await message.answer(
            f"Нужен непустой запрос до {temperature.settings.history_max_chars} символов. "
            "Повторите ввод или отправьте /cancel.",
            parse_mode=None,
        )
        return
    except Exception:
        logger.warning("Temperature preparation failed; no user data logged.")
        await message.answer(
            "Не удалось подготовить тест. Повторите ввод позже или отправьте /cancel.",
            parse_mode=None,
        )
        return
    await state.clear()
    await show_run(message, message.from_user.id, temperature, run_id)


def create_router():
    router = Router(name="temperature")
    router.callback_query.register(action, F.data.startswith("temp:"))
    router.message.register(
        custom_prompt,
        TemperatureInput.prompt,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        ~F.text.in_(BUTTONS),
    )
    return router
