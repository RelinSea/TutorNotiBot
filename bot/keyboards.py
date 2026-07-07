import sqlite3
from calendar import monthrange
from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.models import REMINDER_OPTIONS


BTN_ADD_STUDENT = "➕ Добавить ученика"
BTN_SINGLE_LESSON = "📅 Разовые занятия"
BTN_REGULAR_SCHEDULE = "🔁 Постоянное расписание"
BTN_STUDENTS = "👥 Ученики"
BTN_SCHEDULE = "📖 Расписание"
BTN_SETTINGS = "⚙ Настройки"
BTN_CANCEL = "❌ Отмена"
BTN_BACK = "⬅ Назад"

MONTHS_RU = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADD_STUDENT), KeyboardButton(text=BTN_SINGLE_LESSON)],
            [KeyboardButton(text=BTN_REGULAR_SCHEDULE), KeyboardButton(text=BTN_SCHEDULE)],
            [KeyboardButton(text=BTN_STUDENTS), KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def home_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_SINGLE_LESSON, callback_data="home:single"),
                InlineKeyboardButton(text=BTN_REGULAR_SCHEDULE, callback_data="home:regular"),
            ],
            [
                InlineKeyboardButton(text=BTN_SCHEDULE, callback_data="home:schedule"),
                InlineKeyboardButton(text=BTN_STUDENTS, callback_data="home:students"),
            ],
            [
                InlineKeyboardButton(text=BTN_ADD_STUDENT, callback_data="home:add_student"),
                InlineKeyboardButton(text=BTN_SETTINGS, callback_data="home:settings"),
            ],
        ]
    )

def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        input_field_placeholder="Можно отменить действие",
    )


def wizard_nav_keyboard(
    *,
    back_callback_data: str | None,
    cancel_callback_data: str = "wiz:cancel",
) -> list[list[InlineKeyboardButton]]:
    row: list[InlineKeyboardButton] = []
    if back_callback_data is not None:
        row.append(InlineKeyboardButton(text=BTN_BACK, callback_data=back_callback_data))
    row.append(InlineKeyboardButton(text=BTN_CANCEL, callback_data=cancel_callback_data))
    return [row]


def with_wizard_nav(
    markup: InlineKeyboardMarkup,
    *,
    back_callback_data: str | None,
    cancel_callback_data: str = "wiz:cancel",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[*markup.inline_keyboard, *wizard_nav_keyboard(back_callback_data=back_callback_data, cancel_callback_data=cancel_callback_data)]
    )


def create_lesson_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_SINGLE_LESSON, callback_data="lesson_single")],
            [InlineKeyboardButton(text=BTN_REGULAR_SCHEDULE, callback_data="lesson_regular")],
        ]
    )


def student_multiselect_keyboard(
    students: list[sqlite3.Row],
    selected_ids: set[int],
) -> InlineKeyboardMarkup:
    rows = []
    all_selected = len(selected_ids) == len(students)
    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Все ученики" if all_selected else "☐ Все ученики",
                callback_data="lesson_students:all",
            )
        ]
    )
    for student in students:
        student_id = int(student["id"])
        mark = "✅" if student_id in selected_ids else "☐"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {student['name']}",
                    callback_data=f"lesson_students:toggle:{student_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Далее", callback_data="lesson_students:next")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calendar_keyboard(year: int, month: int, prefix: str = "cal") -> InlineKeyboardMarkup:
    today = date.today()
    first_weekday, days_count = monthrange(year, month)
    rows = [
        [InlineKeyboardButton(text=f"{MONTHS_RU[month]} {year}", callback_data="noop")],
        [
            InlineKeyboardButton(text=day_name, callback_data="noop")
            for day_name in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
        ],
    ]

    week = [InlineKeyboardButton(text=" ", callback_data="noop") for _ in range(first_weekday)]
    for day in range(1, days_count + 1):
        current = date(year, month, day)
        if current < today:
            button = InlineKeyboardButton(text=" ", callback_data="noop")
        else:
            button = InlineKeyboardButton(
                text=str(day),
                callback_data=f"{prefix}:pick:{current.isoformat()}",
            )
        week.append(button)
        if len(week) == 7:
            rows.append(week)
            week = []

    if week:
        week.extend(InlineKeyboardButton(text=" ", callback_data="noop") for _ in range(7 - len(week)))
        rows.append(week)

    prev_year, prev_month = shift_month(year, month, -1)
    next_year, next_month = shift_month(year, month, 1)
    rows.append(
        [
            InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:month:{prev_year}:{prev_month}"),
            InlineKeyboardButton(text="Сегодня", callback_data=f"{prefix}:pick:{today.isoformat()}"),
            InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:month:{next_year}:{next_month}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def multiselect_calendar_keyboard(
    year: int,
    month: int,
    selected_dates: set[str],
    prefix: str = "dates",
) -> InlineKeyboardMarkup:
    today = date.today()
    first_weekday, days_count = monthrange(year, month)
    rows = [
        [InlineKeyboardButton(text=f"{MONTHS_RU[month]} {year}", callback_data="noop")],
        [
            InlineKeyboardButton(text=day_name, callback_data="noop")
            for day_name in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
        ],
    ]

    week = [InlineKeyboardButton(text=" ", callback_data="noop") for _ in range(first_weekday)]
    for day in range(1, days_count + 1):
        current = date(year, month, day)
        current_value = current.isoformat()
        if current < today:
            button = InlineKeyboardButton(text=" ", callback_data="noop")
        else:
            mark = "✅ " if current_value in selected_dates else ""
            button = InlineKeyboardButton(
                text=f"{mark}{day}",
                callback_data=f"{prefix}:toggle:{current_value}",
            )
        week.append(button)
        if len(week) == 7:
            rows.append(week)
            week = []

    if week:
        week.extend(InlineKeyboardButton(text=" ", callback_data="noop") for _ in range(7 - len(week)))
        rows.append(week)

    prev_year, prev_month = shift_month(year, month, -1)
    next_year, next_month = shift_month(year, month, 1)
    rows.append(
        [
            InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:month:{prev_year}:{prev_month}"),
            InlineKeyboardButton(text="✅ Готово", callback_data=f"{prefix}:done"),
            InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:month:{next_year}:{next_month}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def time_keyboard() -> InlineKeyboardMarkup:
    times = [f"{hour:02d}:00" for hour in range(9, 21)]
    rows = []
    for index in range(0, len(times), 3):
        rows.append(
            [
                InlineKeyboardButton(
                    text=value,
                    callback_data=f"lesson_time:{value.replace(':', '')}",
                )
                for value in times[index : index + 3]
            ]
        )
    rows.append([InlineKeyboardButton(text="✏️ Другое время", callback_data="lesson_time:other")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def schedule_kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_SINGLE_LESSON, callback_data="lesson_single")],
            [InlineKeyboardButton(text=BTN_REGULAR_SCHEDULE, callback_data="lesson_regular")],
        ]
    )


def schedule_dates_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗓 По дням недели", callback_data="schedule_dates:weekdays")],
            [InlineKeyboardButton(text="📅 Конкретные даты", callback_data="schedule_dates:custom")],
        ]
    )


def weekday_keyboard(selected_weekdays: set[int]) -> InlineKeyboardMarkup:
    rows = []
    for index in range(0, len(WEEKDAYS_RU), 4):
        row = []
        for weekday in range(index, min(index + 4, len(WEEKDAYS_RU))):
            mark = "✅" if weekday in selected_weekdays else "☐"
            row.append(
                InlineKeyboardButton(
                    text=f"{mark} {WEEKDAYS_RU[weekday]}",
                    callback_data=f"repeat_days:toggle:{weekday}",
                )
            )
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Далее", callback_data="repeat_days:next")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def repeat_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Создать занятия", callback_data="repeat:create")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="repeat:cancel")],
        ]
    )


def lesson_confirm_keyboard(*, create_callback_data: str, back_callback_data: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="✏ Ученики", callback_data="conf:edit:students"),
            InlineKeyboardButton(text="✏ Даты", callback_data="conf:edit:dates"),
        ],
        [
            InlineKeyboardButton(text="✏ Время", callback_data="conf:edit:time"),
            InlineKeyboardButton(text="🔔 Напоминания", callback_data="conf:edit:reminders"),
        ],
        [InlineKeyboardButton(text="✅ Создать", callback_data=create_callback_data)],
    ]
    rows.extend(wizard_nav_keyboard(back_callback_data=back_callback_data))
    return InlineKeyboardMarkup(inline_keyboard=rows)

def schedule_week_keyboard(week_offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀ Неделя", callback_data=f"sch:week:{week_offset - 1}"),
                InlineKeyboardButton(text="Сегодня", callback_data="sch:today"),
                InlineKeyboardButton(text="Неделя ▶", callback_data=f"sch:week:{week_offset + 1}"),
            ],
            [InlineKeyboardButton(text="➕ Создать", callback_data="sch:create")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="home:menu")],
        ]
    )


def schedule_lesson_card_keyboard(
    lesson_id: int,
    *,
    week_offset: int,
    back_callback_data: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏ Изменить",
                    callback_data=f"sch:lesson:edit:{lesson_id}:{week_offset}",
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"sch:lesson:delete:{lesson_id}:{week_offset}",
                ),
            ],
            [InlineKeyboardButton(text=BTN_BACK, callback_data=back_callback_data)],
        ]
    )


def lesson_edit_menu_keyboard(*, lesson_id: int, week_offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Дата",
                    callback_data=f"sch:lesson:field:date:{lesson_id}:{week_offset}",
                ),
                InlineKeyboardButton(
                    text="🕒 Время",
                    callback_data=f"sch:lesson:field:time:{lesson_id}:{week_offset}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Напоминания",
                    callback_data=f"sch:lesson:field:reminders:{lesson_id}:{week_offset}",
                )
            ],
            [InlineKeyboardButton(text=BTN_BACK, callback_data=f"sch:lesson:{lesson_id}:{week_offset}")],
        ]
    )


def schedule_occurrence_card_keyboard(
    *,
    pause_callback_data: str,
    delete_callback_data: str,
    back_callback_data: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏸ Отключить правило", callback_data=pause_callback_data),
                InlineKeyboardButton(text="🗑 Удалить правило", callback_data=delete_callback_data),
            ],
            [InlineKeyboardButton(text=BTN_BACK, callback_data=back_callback_data)],
        ]
    )


def reminder_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for kind, option in REMINDER_OPTIONS.items():
        mark = "✅" if kind in selected else "☐"
        rows.append([InlineKeyboardButton(text=f"{mark} {option.label}", callback_data=f"rem:{kind}")])
    rows.append([InlineKeyboardButton(text="Готово", callback_data="rem:save")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def students_management_keyboard(students: list[sqlite3.Row]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🗑 {student['name']}", callback_data=f"student_del:{student['id']}")]
            for student in students
        ]
    )


def confirm_student_delete_keyboard(student_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=f"student_del_confirm:{student_id}",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="student_del_cancel",
                ),
            ]
        ]
    )


def lesson_actions_keyboard(lesson_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏ Изменить", callback_data=f"lesson_edit:{lesson_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"lesson_del:{lesson_id}"),
            ]
        ]
    )


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return year, month
