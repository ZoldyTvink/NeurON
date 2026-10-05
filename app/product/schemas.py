from datetime import date
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Schedule(StrictModel):
    start_date: date
    end_date: date | None = None
    times: list[str] = Field(min_length=1, max_length=6)
    weekdays: list[int] = Field(
        default_factory=lambda: list(range(7)), min_length=1, max_length=7
    )
    timezone: str = "Europe/Moscow"

    @field_validator("times")
    @classmethod
    def validate_times(cls, values):
        import re

        if any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", v) for v in values):
            raise ValueError("Время должно быть в формате ЧЧ:ММ")
        return sorted(set(values))

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, values):
        if any(v < 0 or v > 6 for v in values):
            raise ValueError("Дни недели: 0–6")
        return sorted(set(values))

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Неизвестный часовой пояс")
        return value

    @model_validator(mode="after")
    def validate_dates(self):
        if abs((self.start_date - date.today()).days) > 366:
            raise ValueError("Начало должно быть в пределах года от текущей даты")
        if self.end_date and not 0 <= (self.end_date - self.start_date).days <= 365:
            raise ValueError(
                "Окончание должно быть не раньше начала и не далее чем через год"
            )
        if self.end_date and (self.end_date - self.start_date).days < 7:
            from datetime import timedelta

            if not any(
                (self.start_date + timedelta(days=i)).weekday() in self.weekdays
                for i in range((self.end_date - self.start_date).days + 1)
            ):
                raise ValueError(
                    "В выбранном периоде нет событий с такими днями недели"
                )
        return self


class PersonalEvent(Schedule):
    title: str = Field(min_length=1, max_length=160)
    kind: Literal["medication", "vitamin", "food", "other"] = "other"
    dosage: str = Field(default="", max_length=160)
    notes: str = Field(default="", max_length=2000)


class Prescription(Schedule):
    name: str = Field(min_length=1, max_length=160)
    form: str = Field(min_length=1, max_length=80)
    strength: str = Field(min_length=1, max_length=80)
    dosage: str = Field(min_length=1, max_length=160)
    units_per_intake: Decimal = Field(gt=0, le=100)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def bounded_course(self):
        if self.end_date is None:
            raise ValueError("Для курса укажите дату окончания")
        return self


class MarkEvent(StrictModel):
    status: Literal["taken", "skipped", "pending", "snoozed"]


class ChatIn(StrictModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatAction(StrictModel):
    action: Literal[
        "none",
        "calendar",
        "plans",
        "booking",
        "personal_event",
        "documents",
        "medications",
    ]
    reply: str = Field(min_length=1, max_length=6000)


class BookingIn(StrictModel):
    slot_id: int = Field(gt=0)
