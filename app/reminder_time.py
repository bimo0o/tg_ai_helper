"""Strict, offline validation of explicit user timezones and reminder instants."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ReminderInputError(ValueError):
    """Safe to show to the user; never includes their input."""


def timezone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or not 1 <= len(name) <= 100:
        raise ReminderInputError("Укажите часовой пояс IANA, например Europe/Moscow.")
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ReminderInputError("Неизвестный часовой пояс IANA.") from None


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
