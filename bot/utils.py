from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram.types import CallbackQuery, Message


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def user_name(event: Message | CallbackQuery) -> str:
    user = event.from_user
    if not user:
        return "Без имени"
    return user.full_name or user.username or str(user.id)


def local_zone(timezone_name: str) -> ZoneInfo:
    return ZoneInfo(timezone_name)
