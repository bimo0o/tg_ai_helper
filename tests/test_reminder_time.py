from datetime import UTC, datetime

import pytest

from app.reminder_time import ReminderInputError, local_instant, reminder_instant, timezone

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_timezone_is_explicit_and_invalid_input_does_not_leak():
    assert timezone("Europe/Moscow").key == "Europe/Moscow"
    for value in ("", "SECRET/invalid", "../private", "a" * 101):
        with pytest.raises(ReminderInputError) as error:
            timezone(value)
        assert value not in str(error.value) if value else True


def test_explicit_offset_must_match_zone_and_future():
    assert reminder_instant("2026-10-23T10:00:00+03:00", "Europe/Moscow", NOW) == datetime(
        2026, 10, 23, 7, tzinfo=UTC
    )
    for value in ("2026-10-23T10:00:00", "2026-10-23T10:00:00Z", "bad", NOW.isoformat()):
        with pytest.raises(ReminderInputError):
            reminder_instant(value, "Europe/Moscow", NOW)


def test_dst_nonexistent_and_ambiguous_wall_times_are_rejected():
    for value in (datetime(2026, 3, 29, 2, 30), datetime(2026, 10, 25, 2, 30)):
        with pytest.raises(ReminderInputError):
            local_instant(value, "Europe/Berlin")
    assert local_instant(datetime(2026, 10, 25, 3, 30), "Europe/Berlin") == datetime(
        2026, 10, 25, 2, 30, tzinfo=UTC
    )


def test_ambiguous_time_can_be_disambiguated_with_correct_offset():
    a = reminder_instant("2026-10-25T02:30:00+02:00", "Europe/Berlin", NOW)
    b = reminder_instant("2026-10-25T02:30:00+01:00", "Europe/Berlin", NOW)
    assert (b - a).total_seconds() == 3600
