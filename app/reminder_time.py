"""Strict, offline validation of explicit user timezones and reminder instants."""

import re
from datetime import UTC, datetime, timedelta
from datetime import timezone as FixedTimezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ReminderInputError(ValueError):
    """Safe to show to the user; never includes their input."""


def timezone(name: str):
    if not isinstance(name, str) or not 1 <= len(name) <= 100:
        raise ReminderInputError("Укажите часовой пояс IANA, например Europe/Moscow.")
    if re.fullmatch(r"UTC[+-]\d{2}:\d{2}", name):
        minutes = int(name[4:6]) * 60 + int(name[7:9])
        if name[3] == "-":
            minutes = -minutes
        if int(name[7:9]) >= 60 or not -720 <= minutes <= 840:
            raise ReminderInputError("Некорректное смещение UTC.")
        return FixedTimezone(timedelta(minutes=minutes), name)
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ReminderInputError("Неизвестный часовой пояс IANA.") from None


def timezone_from_clock(value: str, now: datetime) -> str:
    """Infer a fixed offset, never an IANA/DST region, from a recent local clock."""
    if now.tzinfo is None:
        raise ValueError("Clock must be timezone-aware")
    value = value.strip()
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}", value):
            hour, minute = map(int, value.split(":"))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError
            utc = now.astimezone(UTC).replace(second=0, microsecond=0)
            candidates = [
                offset
                for offset in range(-720, 841, 15)
                if min(
                    (hour * 60 + minute - (utc.hour * 60 + utc.minute + offset)) % 1440,
                    ((utc.hour * 60 + utc.minute + offset) - hour * 60 - minute) % 1440,
                )
                <= 3
            ]
        else:
            local = datetime.fromisoformat(value)
            if local.tzinfo is not None or not (" " in value or "T" in value):
                raise ValueError
            difference = (local - now.astimezone(UTC).replace(tzinfo=None)).total_seconds() / 60
            candidates = [
                offset for offset in range(-720, 841, 15) if abs(offset - difference) <= 3
            ]
    except ValueError:
        raise ReminderInputError("Напиши текущее местное время: ЧЧ:ММ, например 18:35.") from None
    if len(candidates) != 1:
        raise ReminderInputError(
            "Не удалось однозначно определить смещение. Пришли текущие дату и время: "
            "ГГГГ-ММ-ДД ЧЧ:ММ."
        )
    offset = candidates[0]
    return f"UTC{'+' if offset >= 0 else '-'}{abs(offset) // 60:02}:{abs(offset) % 60:02}"


def local_instant(value: datetime, zone: str) -> datetime:
    """Resolve wall time only when it maps to exactly one UTC instant (DST-safe)."""
    if value.tzinfo is not None:
        raise ReminderInputError("Для местного времени не указывайте смещение UTC.")
    tz = timezone(zone)
    candidates = set()
    for fold in (0, 1):
        instant = value.replace(tzinfo=tz, fold=fold).astimezone(UTC)
        if instant.astimezone(tz).replace(tzinfo=None) == value:
            candidates.add(instant)
    if not candidates:
        raise ReminderInputError("Это местное время не существует из-за перевода часов.")
    if len(candidates) != 1:
        raise ReminderInputError("Это время повторяется при переводе часов. Укажите смещение UTC.")
    return candidates.pop()


def reminder_instant(value: str, zone: str, now: datetime) -> datetime:
    """Require an offset and check it against the selected IANA timezone."""
    tz = timezone(zone)
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ReminderInputError("Нужна дата ISO 8601 со смещением UTC.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReminderInputError("У даты должно быть явное смещение UTC.")
    instant = parsed.astimezone(UTC)
    if parsed.utcoffset() != instant.astimezone(tz).utcoffset():
        raise ReminderInputError("Смещение даты не соответствует выбранному часовому поясу.")
    if now.tzinfo is None:
        raise ValueError("Clock must be timezone-aware")
    if instant <= now:
        raise ReminderInputError("Время напоминания должно быть в будущем.")
    return instant
