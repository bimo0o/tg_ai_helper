"""Shared prompt and format criterion for the admin and CLI temperature experiments."""

import re

PROMPT = (
    "Объясни начинающему программисту разницу между списком и кортежем в Python "
    "через одну бытовую аналогию.\n\n"
    "Не используй аналогии с коробками, контейнерами или покупками.\n\n"
    "Ответ ровно из трёх непустых строк, каждая — один пронумерованный пункт:\n"
    "1. Аналогия.\n"
    "2. Как она объясняет изменяемость списка и неизменяемость кортежа.\n"
    "3. Где аналогия перестаёт быть точной.\n\n"
    "До 120 слов. Обязательно учитывай, что кортеж может содержать изменяемый объект. "
    "Не добавляй вступление и заключение."
)


def follows_format(text: str) -> bool:
    lines = text.strip().splitlines()
    return len(lines) == 3 and all(
        re.fullmatch(rf"{number}\.\s+\S.*", line) is not None
        for number, line in enumerate(lines, 1)
    )
