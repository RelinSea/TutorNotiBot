from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class ReminderOption:
    label: str
    delta: timedelta


REMINDER_OPTIONS = {
    "day": ReminderOption("За день", timedelta(days=1)),
    "hour": ReminderOption("За час", timedelta(hours=1)),
    "10m": ReminderOption("За 10 минут", timedelta(minutes=10)),
}
