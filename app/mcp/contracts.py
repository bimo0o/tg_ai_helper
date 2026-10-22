"""Shared strict contracts, published through tools/list and checked on both ends."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ReminderText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class WeatherInput(Contract):
    city: ShortText


class ScheduleInput(Contract):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", max_length=10)

    @field_validator("date")
    @classmethod
    def iso_date(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Expected YYYY-MM-DD")
        return value


class ReminderInput(Contract):
    text: ReminderText
    remind_at: str = Field(max_length=64, json_schema_extra={"format": "date-time"})

    @field_validator("remind_at")
    @classmethod
    def aware_date(cls, value):
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("UTC offset is required")
        return value


class Place(Contract):
    city: ShortText
    country: ShortText
    region: str = Field(max_length=100)


class Observation(Contract):
    temperature_c: float = Field(ge=-100, le=70)
    weather_code: int = Field(ge=0, le=99)
    wind_speed_kmh: float = Field(ge=0, le=500)
    wind_unit: Literal["km/h"] = "km/h"
    observed_at: str = Field(max_length=40)
    timezone: ShortText


class WeatherOutput(Contract):
    status: Literal["ok", "ambiguous", "not_found"]
    places: list[Place] = Field(max_length=10)
    observation: Observation | None = None
    source: Literal["https://open-meteo.com/"] = "https://open-meteo.com/"

    @model_validator(mode="after")
    def consistent_result(self):
        if self.status == "ok" and (len(self.places) != 1 or self.observation is None):
            raise ValueError("Successful observation needs one place and weather data")
        if self.status != "ok" and self.observation is not None:
            raise ValueError("Non-success result must not contain an observation")
        if self.status == "not_found" and self.places:
            raise ValueError("Not-found result cannot contain candidates")
        if self.status == "ambiguous" and len(self.places) < 2:
            raise ValueError("Ambiguous result needs multiple candidates")
        return self


class Lesson(Contract):
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(max_length=200)

    @field_validator("end")
    @classmethod
    def after_start(cls, value, info):
        if "start" in info.data and value <= info.data["start"]:
            raise ValueError("Lesson must end after it starts")
        return value


class ScheduleOutput(Contract):
    date: str
    timezone: ShortText
    lessons: list[Lesson] = Field(max_length=30)
    source: Literal["configured_schedule"] = "configured_schedule"


class WeekOutput(Contract):
    timezone: ShortText
    days: list[ScheduleOutput] = Field(min_length=7, max_length=7)


class ReminderOutput(Contract):
    id: UUID
    remind_at: datetime
    timezone: ShortText
    created: bool


INPUTS = {
    "get_weather": WeatherInput,
    "get_schedule": ScheduleInput,
    "add_reminder": ReminderInput,
}
OUTPUTS = {
    "get_weather": WeatherOutput,
    "get_schedule": ScheduleOutput,
    "add_reminder": ReminderOutput,
}
DESCRIPTIONS = {
    "get_weather": "Текущая погода в явно указанном городе; неоднозначный город требует уточнения.",
    "get_schedule": "Занятия на дату YYYY-MM-DD из настроенного расписания.",
    "add_reminder": "Создать напоминание после отдельного подтверждения пользователя.",
}
