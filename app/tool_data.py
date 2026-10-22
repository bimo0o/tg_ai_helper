"""Read-only providers with configured schedule paths and fixed weather endpoints."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout

from app.mcp.contracts import (
    Lesson,
    Observation,
    Place,
    ScheduleInput,
    ScheduleOutput,
    WeatherInput,
    WeatherOutput,
    WeekOutput,
)
from app.reminder_time import timezone


class ToolDataError(ValueError):
    """Safe, non-content error for protocol adapters."""


class Schedule:
    def __init__(self, data: dict, zone: str, *, clock=None):
        timezone(zone)
        if not isinstance(data, dict) or len(data) > 366:
            raise ToolDataError("Invalid schedule")
        self.zone = zone
        self.clock = clock or (lambda: datetime.now(UTC))
        self.days = {}
        for day, lessons in data.items():
            ScheduleInput(date=day)
            if not isinstance(lessons, list) or len(lessons) > 30:
                raise ToolDataError("Invalid schedule")
            self.days[day] = [Lesson.model_validate(lesson) for lesson in lessons]

    @classmethod
    def load(cls, path: str, zone: str):
        try:
            with Path(path).open("rb") as source:
                raw = source.read(65537)
            if len(raw) > 65536:
                raise ValueError
            return cls(json.loads(raw), zone)
        except (OSError, ValueError):
            raise ToolDataError("Schedule unavailable or invalid") from None

    def day(self, value: str) -> ScheduleOutput:
        ScheduleInput(date=value)
        return ScheduleOutput(date=value, timezone=self.zone, lessons=self.days.get(value, []))

    def week(self) -> WeekOutput:
        today = self.clock().astimezone(timezone(self.zone)).date()
        monday = today - timedelta(days=today.weekday())
        return WeekOutput(
            timezone=self.zone,
            days=[self.day((monday + timedelta(days=i)).isoformat()) for i in range(7)],
        )


class Weather:
    def __init__(self, session: ClientSession):
        self.session = session

    async def _get(self, url: str, params: dict) -> dict:
        async with self.session.get(
            url, params=params, timeout=ClientTimeout(total=7), allow_redirects=False
        ) as response:
            if response.status != 200:
                raise ToolDataError("Weather service unavailable")
            try:
                raw = await response.content.readexactly(65537)
            except asyncio.IncompleteReadError as error:
                raw = error.partial
            if len(raw) > 65536:
                raise ToolDataError("Weather response too large")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ToolDataError("Invalid weather response")
            return value

    async def current(self, city: str) -> WeatherOutput:
        WeatherInput(city=city)
        try:
            async with asyncio.timeout(15):
                data = await self._get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    {"name": city, "count": 10, "language": "ru", "format": "json"},
                )
                results = data.get("results", [])
                if not isinstance(results, list) or len(results) > 10:
                    raise ToolDataError("Invalid geocoding response")
                places = [
                    Place(city=r["name"], country=r["country"], region=r.get("admin1", ""))
                    for r in results
                ]
                if len(places) != 1:
                    return WeatherOutput(
                        status="ambiguous" if places else "not_found", places=places
                    )
                location = results[0]
                lat, lon = float(location["latitude"]), float(location["longitude"])
                if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ToolDataError("Invalid coordinates")
                data = await self._get(
                    "https://api.open-meteo.com/v1/forecast",
                    {
                        "latitude": lat,
                        "longitude": lon,
                        "current": "temperature_2m,weather_code,wind_speed_10m",
                        "temperature_unit": "celsius",
                        "wind_speed_unit": "kmh",
                        "timezone": "auto",
                    },
                )
                current = data["current"]
                units = data["current_units"]
                if units["temperature_2m"] != "°C" or units["wind_speed_10m"] != "km/h":
                    raise ToolDataError("Unexpected weather units")
                zone = data["timezone"]
                timezone(zone)
                datetime.fromisoformat(current["time"])
                return WeatherOutput(
                    status="ok",
                    places=places,
                    observation=Observation(
                        temperature_c=current["temperature_2m"],
                        weather_code=current["weather_code"],
                        wind_speed_kmh=current["wind_speed_10m"],
                        observed_at=current["time"],
                        timezone=zone,
                    ),
                )
        except (KeyError, TypeError, ValueError, TimeoutError, OSError, ClientError):
            raise ToolDataError("Weather unavailable or invalid response") from None
