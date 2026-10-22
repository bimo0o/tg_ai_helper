"""Telegram keyboards shared by commands and the conversational interface."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.llm import TEMPERATURES
from app.music_profile import FIELDS

MODE_BUTTONS = {
    "💬 Диалог": "chat",
    "🔎 Поиск": "web",
    "🎵 Музыка": "music",
    "📚 Учёба": "study",
    "🌐 Перевод": "translate",
}
PROFILE = "👤 Профиль"
SETTINGS = "⚙️ Настройки"
RESET = "🧹 Новый диалог"
MENU = "☰ Меню"
ADMIN = "🛠 Админка"
BUTTONS = {*MODE_BUTTONS, PROFILE, SETTINGS, RESET, MENU, ADMIN}
WELCOME = (
    "Выбери режим кнопками ниже, затем напиши вопрос.\n\n"
    "💬 Диалог — вопросы на любую тему\n"
    "🔎 Поиск — ответы с web-источниками; каждый вопрос запускает платный поиск\n"
    "🎵 Музыка — создание музыки, сведение и плагины\n"
    "📚 Учёба — программирование\n"
    "🌐 Перевод — укажи целевой язык в сообщении\n\n"
    "В профиле можно сохранить DAW и плагины, в настройках — изменить вариативность ответов. "
    "Раздел «Обо мне» хранит краткие предпочтения для персонализации. "
    "«Новый диалог» очищает историю с подтверждением, сохраняя профиль и настройки.\n"
    "Если режим ещё не выбран, включена «Учёба». Команды также работают."
)


def main_keyboard(is_admin=False) -> ReplyKeyboardMarkup:
    rows = [
        ["💬 Диалог", "🔎 Поиск"],
        ["🎵 Музыка", "📚 Учёба", "🌐 Перевод"],
        [PROFILE, SETTINGS],
        [RESET, MENU],
    ]
    if is_admin:
        rows.append([ADMIN])
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напиши вопрос или выбери режим",
    )


def button(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data="ui:" + action)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button("Отмена · в меню", "home")]])


def profile_keyboard() -> InlineKeyboardMarkup:
    fields = [button(label, "field:" + key) for key, label in FIELDS.items()]
    rows = [fields[index : index + 2] for index in range(0, len(fields), 2)]
    rows.insert(0, [InlineKeyboardButton(text="Обо мне · персонализация", callback_data="pm:open")])
    rows.extend([[button("Удалить профиль", "profile_clear")], [button("В меню", "home")]])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_keyboard(temperature: float, is_admin=False) -> InlineKeyboardMarkup:
    rows = [
        [
            button(("✓ " if value == temperature else "") + f"{value:.1f}", f"temp:{value:.1f}")
            for value in TEMPERATURES
        ]
    ]
    if is_admin:
        rows.append([button("Модель и статистика", "admin")])
    rows.append([button("В меню", "home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirmation_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[button("Да, очистить", action), button("Отмена", "home")]]
    )
