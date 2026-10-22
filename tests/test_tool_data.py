import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from app.tool_data import Schedule, ToolDataError, Weather


@pytest.mark.parametrize(
    "now,zone,monday,sunday",
    [
        (datetime(2027, 1, 1, tzinfo=UTC), "UTC", "2026-12-28", "2027-01-03"),
        (datetime(2026, 11, 1, tzinfo=UTC), "UTC", "2026-10-26", "2026-11-01"),
        (datetime(2026, 11, 1, 23, tzinfo=UTC), "Europe/Moscow", "2026-11-02", "2026-11-08"),
    ],
)
def test_week_boundaries_use_configured_timezone_and_injected_clock(now, zone, monday, sunday):
    week = Schedule({}, zone, clock=lambda: now).week()
    assert week.days[0].date == monday and week.days[-1].date == sunday


def test_schedule_load_is_bounded_and_does_not_follow_model_supplied_paths(tmp_path):
    source = tmp_path / "synthetic.json"
    source.write_text(
        '{"2027-01-01": [{"start":"10:00","end":"11:00",'
        '"title":"Synthetic lesson","location":"Test room"}]}',
        encoding="utf-8",
    )
    schedule = Schedule.load(str(source), "UTC")
    assert schedule.day("2027-01-01").lessons[0].title == "Synthetic lesson"
    assert schedule.day("2027-01-02").lessons == []
    source.write_text("x" * 65537, encoding="utf-8")
    with pytest.raises(ToolDataError):
        Schedule.load(str(source), "UTC")


async def test_ambiguous_city_never_triggers_forecast_for_first_candidate():
    weather = Weather(None)
    weather._get = AsyncMock(
        return_value={
            "results": [
                {"name": "Test", "country": "A", "admin1": "North"},
                {"name": "Test", "country": "B", "admin1": "South"},
            ]
        }
    )
    result = await weather.current("Test")
    assert result.status == "ambiguous"
    assert [p.country for p in result.places] == ["A", "B"]
    assert weather._get.await_count == 1


async def test_weather_units_observation_and_fixed_endpoints():
    weather = Weather(None)
    weather._get = AsyncMock(
        side_effect=[
            {"results": [{"name": "Test", "country": "A", "latitude": 50, "longitude": 10}]},
            {
                "current": {
                    "temperature_2m": 12,
                    "weather_code": 3,
                    "wind_speed_10m": 5,
                    "time": "2027-01-01T10:00",
                },
                "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
                "timezone": "UTC",
            },
        ]
    )
    result = await weather.current("Test")
    assert result.status == "ok" and result.observation.temperature_c == 12
    calls = weather._get.await_args_list
    assert [call.args[0] for call in calls] == [
        "https://geocoding-api.open-meteo.com/v1/search",
        "https://api.open-meteo.com/v1/forecast",
    ]
    assert calls[1].args[1]["wind_speed_unit"] == "kmh"


@pytest.mark.parametrize("value", [{}, {"results": []}, {"results": "invalid"}, {"results": [{}]}])
async def test_empty_and_malformed_geocoding_have_distinct_results(value):
    weather = Weather(None)
    weather._get = AsyncMock(return_value=value)
    if value in ({}, {"results": []}):
        assert (await weather.current("Test")).status == "not_found"
    else:
        with pytest.raises(ToolDataError):
            await weather.current("Test")


async def test_weather_http_body_is_bounded_and_redirects_are_disabled():
    response = AsyncMock()
    response.status = 200
    raw = json.dumps({"results": []}).encode()
    response.content.readexactly.side_effect = asyncio.IncompleteReadError(raw, 65537)
    context = AsyncMock()
    context.__aenter__.return_value = response
    session = Mock(get=Mock(return_value=context))
    weather = Weather(session)
    assert (await weather.current("Test")).status == "not_found"
    assert session.get.call_args.kwargs["allow_redirects"] is False
    response.content.readexactly.assert_awaited_once_with(65537)
    response.content.readexactly.side_effect = None
    response.content.readexactly.return_value = b"x" * 65537
    with pytest.raises(ToolDataError):
        await weather.current("Test")
    response.status = 503
    with pytest.raises(ToolDataError):
        await weather.current("Test")
