import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

from aiogram import Bot, html

from bot.config import Settings
from bot.database import Database
from bot.models import REMINDER_OPTIONS
from bot.utils import local_zone, parse_utc


LOG = logging.getLogger(__name__)


async def reminder_worker(bot: Bot, db: Database, settings: Settings) -> None:
    zone = local_zone(settings.timezone)
    await asyncio.sleep(3)

    while True:
        try:
            await send_due_single_lesson_reminders(bot, db, zone)
            await send_due_schedule_rule_reminders(bot, db, zone)
        except Exception:
            LOG.exception("Reminder worker failed")

        await asyncio.sleep(settings.reminder_poll_seconds)


async def send_due_single_lesson_reminders(bot: Bot, db: Database, zone) -> None:
    reminders = db.due_reminders()
    for reminder in reminders:
        starts_local = parse_utc(reminder["starts_at"]).astimezone(zone)
        option = REMINDER_OPTIONS.get(reminder["kind"])
        kind_label = option.label.lower() if option else "скоро"
        text = reminder_text(kind_label, starts_local, reminder["teacher_name"])
        try:
            await bot.send_message(reminder["student_telegram_id"], text)
        except Exception:
            LOG.exception("Failed to send reminder %s", reminder["reminder_id"])
        else:
            db.mark_reminder_sent(reminder["reminder_id"])


async def send_due_schedule_rule_reminders(bot: Bot, db: Database, zone) -> None:
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(zone)
    max_delta = max(option.delta for option in REMINDER_OPTIONS.values())
    window_until = now_local + max_delta + timedelta(days=1)

    for rule in db.active_schedule_rules():
        for starts_local in rule_occurrences(rule, zone, now_local.date(), window_until.date()):
            starts_utc = starts_local.astimezone(timezone.utc)
            if starts_utc <= now_utc:
                continue

            occurrence_key = f"rule:{rule['id']}:{starts_utc.isoformat(timespec='minutes')}"
            reminder_kinds = parse_csv(rule["reminders"])
            for kind in reminder_kinds:
                option = REMINDER_OPTIONS.get(kind)
                if option is None:
                    continue
                remind_at = starts_utc - option.delta
                if remind_at > now_utc:
                    continue
                if db.reminder_log_exists(occurrence_key, kind):
                    continue

                text = reminder_text(option.label.lower(), starts_local, rule["teacher_name"])
                try:
                    await bot.send_message(rule["student_telegram_id"], text)
                except Exception:
                    LOG.exception(
                        "Failed to send schedule reminder rule=%s occurrence=%s kind=%s",
                        rule["id"],
                        occurrence_key,
                        kind,
                    )
                else:
                    db.mark_rule_reminder_sent(occurrence_key, kind)


def reminder_text(kind_label: str, starts_local: datetime, teacher_name: str) -> str:
    return (
        "📚 Напоминание\n\n"
        f"{kind_label} у вас урок английского.\n"
        f"Когда: {starts_local:%d.%m.%Y %H:%M}\n"
        f"Преподаватель: {html.quote(teacher_name)}"
    )


def rule_occurrences(rule, zone, start_date, end_date) -> list[datetime]:
    weekdays = set(parse_int_csv(rule["weekdays"]))
    rule_start = datetime.fromisoformat(rule["start_date"]).date()
    rule_end = datetime.fromisoformat(rule["end_date"]).date() if rule["end_date"] else end_date
    current = max(start_date, rule_start)
    end = min(end_date, rule_end)
    lesson_time = time.fromisoformat(rule["lesson_time"])

    values = []
    while current <= end:
        if current.weekday() in weekdays:
            values.append(datetime.combine(current, lesson_time, tzinfo=zone))
        current += timedelta(days=1)
    return values


def parse_csv(value: str) -> list[str]:
    return [item for item in value.split(",") if item]


def parse_int_csv(value: str) -> list[int]:
    return [int(item) for item in parse_csv(value)]
