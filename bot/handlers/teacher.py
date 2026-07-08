import sqlite3
from datetime import date, datetime, time, timedelta

from aiogram import Bot, F, Router, html
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import Settings
from bot.database import Database
from bot.models import REMINDER_OPTIONS
from bot.keyboards import (
    BTN_ADD_STUDENT,
    BTN_REGULAR_SCHEDULE,
    BTN_SCHEDULE,
    BTN_SETTINGS,
    BTN_SINGLE_LESSON,
    BTN_STUDENTS,
    calendar_keyboard,
    confirm_student_delete_keyboard,
    create_lesson_keyboard,
    lesson_actions_keyboard,
    home_inline_keyboard,
    main_menu,
    multiselect_calendar_keyboard,
    reminder_keyboard,
    repeat_preview_keyboard,
    schedule_dates_mode_keyboard,
    schedule_lesson_card_keyboard,
    schedule_occurrence_card_keyboard,
    schedule_week_keyboard,
    lesson_confirm_keyboard,
    lesson_edit_menu_keyboard,
    student_multiselect_keyboard,
    students_management_keyboard,
    time_keyboard,
    with_wizard_nav,
    weekday_keyboard,
    WEEKDAYS_RU,
)
from bot.utils import local_zone, parse_utc, to_utc_iso, user_name
from bot.screens import ScreenContext, render_screen


router = Router(name=__name__)


class LessonForm(StatesGroup):
    students = State()
    schedule_dates_mode = State()
    date = State()
    weekdays = State()
    time = State()
    custom_time = State()
    start_date = State()
    end_date = State()
    reminders = State()
    confirm = State()

class ScheduleScreen(StatesGroup):
    view = State()

def reminders_human(reminders: list[str]) -> str:
    labels: list[str] = []
    for kind in reminders:
        option = REMINDER_OPTIONS.get(kind)
        labels.append(option.label if option is not None else kind)
    return ", ".join(labels)


def wizard_progress(step: int, total: int) -> str:
    filled = max(0, min(step, total))
    bar = "🟩" * filled + "⬜" * max(0, total - filled)
    return f"{bar}\nШаг {step} из {total}\n\n"


async def wizard_edit(
    state: FSMContext,
    *,
    text: str,
    reply_markup: object | None,
    message: Message | None = None,
) -> None:
    if message is None:
        return
    data = await state.get_data()
    ctx: ScreenContext | None = None
    screen_message_id = data.get("screen_message_id")
    chat_id = data.get("screen_chat_id")
    if screen_message_id is not None and chat_id is not None:
        ctx = ScreenContext(chat_id=int(chat_id), message_id=int(screen_message_id))
    # For wizards we only support inline markups here.
    inline_markup = reply_markup if isinstance(reply_markup, InlineKeyboardMarkup) else None
    new_ctx = await render_screen(message=message, ctx=ctx, text=text, reply_markup=inline_markup)
    await state.update_data(screen_message_id=new_ctx.message_id, screen_chat_id=new_ctx.chat_id)


async def require_owner(message: Message, db: Database) -> sqlite3.Row | None:
    teacher = db.get_teacher_by_telegram(message.from_user.id)
    if teacher is None:
        await message.answer("Нажмите /start, чтобы зарегистрироваться как репетитор.")
        return None
    return teacher


@router.message(CommandStart(deep_link=False))
async def start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()

    existing_teacher = db.get_teacher_by_telegram(message.from_user.id)
    if existing_teacher is not None:
        await message.answer(
            f"Здравствуйте, {html.quote(existing_teacher['name'])}!",
            reply_markup=main_menu(),
        )
        return

    teacher = db.upsert_teacher(message.from_user.id, user_name(message))
    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Я помогу управлять расписанием занятий и автоматически напоминать ученикам об уроках.\n\n"
        "✅ Репетитор зарегистрирован\n\n"
        "🟩⬜⬜\n\n"
        "Следующий шаг: добавьте первого ученика.",
        reply_markup=main_menu(),
    )


@router.message(Command("stop"))
async def stop(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    if db.delete_teacher_by_telegram(message.from_user.id):
        await message.answer(
            "Готово. Вы удалены из списка репетиторов.\n\n"
            "Если захотите снова вести расписание, отправьте /start."
        )
        return

    if db.is_student(message.from_user.id):
        await message.answer("Вы не зарегистрированы как репетитор. Как ученик вы продолжите получать напоминания.")
        return

    await message.answer("Вы не зарегистрированы как репетитор. Чтобы начать, отправьте /start.")


@router.message(F.text == BTN_ADD_STUDENT)
async def add_student(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    await state.clear()
    teacher = await require_owner(message, db)
    if teacher is None:
        return

    bot_info = await bot.get_me()
    code = db.create_invite(teacher["id"])
    link = f"https://t.me/{bot_info.username}?start={code}"
    await message.answer(
        "✅ Ссылка создана\n\n"
        "🟩⬜⬜\n\n"
        "Отправьте её ученику:\n\n"
        f"<code>{html.quote(link)}</code>\n\n"
        "Когда ученик подключится, я сообщу вам здесь."
    )


@router.message(F.text == BTN_STUDENTS)
async def show_students(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    teacher = await require_owner(message, db)
    if teacher is None:
        return

    students = db.list_students(teacher["id"])
    if not students:
        await message.answer("Пока нет учеников. Нажмите «➕ Добавить ученика».")
        return

    lines = [f"👥 Ученики ({len(students)})", ""]
    lines.extend(f"• {html.quote(student['name'])}" for student in students)
    lines.append("")
    lines.append("Для удаления выберите ученика ниже.")
    await message.answer(
        "\n".join(lines),
        reply_markup=students_management_keyboard(students),
    )


@router.message(F.text == BTN_SINGLE_LESSON)
async def single_lesson(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    teacher = await require_owner(message, db)
    if teacher is None:
        return
    await start_lesson_flow(message, state, db, teacher["id"], "single")


@router.message(F.text == BTN_REGULAR_SCHEDULE)
async def regular_schedule(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    teacher = await require_owner(message, db)
    if teacher is None:
        return
    await start_lesson_flow(message, state, db, teacher["id"], "regular")


@router.callback_query(StateFilter(None), F.data == "lesson_single")
async def single_lesson_from_button(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    await start_lesson_flow(callback.message, state, db, teacher["id"], "single")
    await callback.answer()


@router.callback_query(StateFilter(None), F.data == "lesson_regular")
async def regular_schedule_from_button(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    await start_lesson_flow(callback.message, state, db, teacher["id"], "regular")
    await callback.answer()


async def start_lesson_flow(
    message: Message,
    state: FSMContext,
    db: Database,
    teacher_id: int,
    mode: str,
) -> None:
    students = db.list_students(teacher_id)
    if not students:
        await message.answer(
            "У вас пока нет учеников.\n\n"
            "Нажмите ➕ Добавить ученика, получите ссылку и отправьте её ученику.",
            reply_markup=main_menu(),
        )
        return

    # Сохраняем screen_message_id из текущего стейта (если есть), чтобы
    # wizard_edit мог отредактировать уже существующее сообщение, а не создавать новое.
    existing_data = await state.get_data()
    prev_screen_message_id = existing_data.get("screen_message_id")
    prev_screen_chat_id = existing_data.get("screen_chat_id")

    await state.clear()
    await state.update_data(
        mode=mode,
        teacher_id=teacher_id,
        selected_student_ids=[],
    )
    if prev_screen_message_id is not None and prev_screen_chat_id is not None:
        await state.update_data(
            screen_message_id=prev_screen_message_id,
            screen_chat_id=prev_screen_chat_id,
        )
    await state.set_state(LessonForm.students)

    title = "Разовые занятия" if mode == "single" else "Постоянное расписание"
    kb = with_wizard_nav(
        student_multiselect_keyboard(students, set()),
        back_callback_data="wiz:exit",
    )
    await wizard_edit(
        state,
        message=message,
        text=f"{title}\n\n{wizard_progress(1, 5)}👥 Выберите одного или нескольких учеников:",
        reply_markup=kb,
    )

@router.callback_query(
    StateFilter(LessonForm.students, LessonForm.schedule_dates_mode, LessonForm.date, LessonForm.weekdays, LessonForm.time, LessonForm.custom_time, LessonForm.start_date, LessonForm.end_date, LessonForm.reminders, LessonForm.confirm),
    F.data == "wiz:cancel",
)
async def cancel_lesson_flow_inline(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Действие отменено.\n\nВыберите действие:", reply_markup=home_inline_keyboard())
    # Сохраняем ID этого сообщения, чтобы следующий wizard мог его отредактировать
    await state.update_data(
        screen_message_id=callback.message.message_id,
        screen_chat_id=callback.message.chat.id,
    )
    await callback.answer()


@router.callback_query(
    StateFilter(LessonForm.students, LessonForm.schedule_dates_mode, LessonForm.date, LessonForm.weekdays, LessonForm.time, LessonForm.custom_time, LessonForm.start_date, LessonForm.end_date, LessonForm.reminders, LessonForm.confirm),
    F.data == "wiz:exit",
)
async def exit_lesson_flow_inline(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Выберите действие:", reply_markup=home_inline_keyboard())
    # Сохраняем ID этого сообщения, чтобы следующий wizard мог его отредактировать
    await state.update_data(
        screen_message_id=callback.message.message_id,
        screen_chat_id=callback.message.chat.id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("home:"))
async def home_router(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings, bot: Bot) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    if action == "menu":
        await state.clear()
        await callback.message.edit_text("Выберите действие:", reply_markup=home_inline_keyboard())
        await state.update_data(
            screen_message_id=callback.message.message_id,
            screen_chat_id=callback.message.chat.id,
        )
        await callback.answer()
        return
    if action == "schedule":
        await render_schedule_week(
            callback.message,
            db,
            settings,
            teacher_id=int(teacher["id"]),
            week_offset=0,
            edit_message_id=callback.message.message_id,
        )
        await callback.answer()
        return
    if action == "students":
        students = db.list_students(int(teacher["id"]))
        if not students:
            await callback.message.edit_text("Пока нет учеников.", reply_markup=home_inline_keyboard())
        else:
            lines = [f"👥 Ученики ({len(students)})", ""]
            lines.extend(f"• {html.quote(student['name'])}" for student in students)
            lines.append("")
            lines.append("Для удаления выберите ученика ниже.")
            await callback.message.edit_text(
                "\n".join(lines),
                reply_markup=students_management_keyboard(students),
            )
        await callback.answer()
        return
    if action == "settings":
        await callback.message.edit_text(
            "⚙ Настройки\n\n"
            f"Часовой пояс: {html.quote(settings.timezone)}\n"
            "Напоминания по умолчанию: за день и за час.",
            reply_markup=home_inline_keyboard(),
        )
        await callback.answer()
        return
    if action == "add_student":
        bot_info = await bot.get_me()
        code = db.create_invite(int(teacher["id"]))
        link = f"https://t.me/{bot_info.username}?start={code}"
        await callback.message.edit_text(
            "✅ Ссылка создана\n\n"
            "Отправьте её ученику:\n\n"
            f"<code>{html.quote(link)}</code>",
            reply_markup=home_inline_keyboard(),
        )
        await callback.answer()
        return
    if action == "single":
        await start_lesson_flow(callback.message, state, db, int(teacher["id"]), "single")
        await callback.answer()
        return
    if action == "regular":
        await start_lesson_flow(callback.message, state, db, int(teacher["id"]), "regular")
        await callback.answer()
        return
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(LessonForm.students, F.data.startswith("lesson_students:"))
async def select_students(
    callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings
) -> None:
    data = await state.get_data()
    teacher_id = int(data["teacher_id"])
    students = db.list_students(teacher_id)
    selected_ids = set(data.get("selected_student_ids", []))
    parts = callback.data.split(":")

    if parts[1] == "all":
        all_ids = {int(student["id"]) for student in students}
        selected_ids = set() if selected_ids == all_ids else all_ids
        await state.update_data(selected_student_ids=sorted(selected_ids))
        await callback.message.edit_reply_markup(
            reply_markup=with_wizard_nav(
                student_multiselect_keyboard(students, selected_ids),
                back_callback_data="wiz:exit",
            )
        )
        await callback.answer()
        return

    if parts[1] == "toggle":
        student_id = int(parts[2])
        if student_id in selected_ids:
            selected_ids.remove(student_id)
        else:
            selected_ids.add(student_id)
        await state.update_data(selected_student_ids=sorted(selected_ids))
        await callback.message.edit_reply_markup(
            reply_markup=with_wizard_nav(
                student_multiselect_keyboard(students, selected_ids),
                back_callback_data="wiz:exit",
            )
        )
        await callback.answer()
        return

    if parts[1] == "next":
        if not selected_ids:
            await callback.answer("Выберите хотя бы одного ученика.", show_alert=True)
            return
        await state.update_data(selected_student_ids=sorted(selected_ids))

        data = await state.get_data()
        if data.get("return_to_confirm") is True:
            await state.update_data(return_to_confirm=False)
            reminders = list(data.get("reminders", ["day", "hour"]))
            await show_confirm_screen(callback, state, db, settings, reminders)
            await callback.answer()
            return

        if data["mode"] == "single":
            await state.set_state(LessonForm.date)
            today = date.today()
            await state.update_data(selected_dates=[])
            await callback.message.edit_text(
                f"{wizard_progress(2, 5)}📅 Выберите одну или несколько дат:",
                reply_markup=with_wizard_nav(
                    multiselect_calendar_keyboard(today.year, today.month, set()),
                    back_callback_data="wiz:back:students",
                ),
            )
        else:
            await state.set_state(LessonForm.schedule_dates_mode)
            await callback.message.edit_text(
                f"{wizard_progress(2, 5)}Как задать занятия?\n\n"
                "🗓 По дням недели — создаёт постоянное расписание.\n"
                "📅 Конкретные даты — создаёт отдельные занятия на выбранные даты.",
                reply_markup=with_wizard_nav(
                    schedule_dates_mode_keyboard(),
                    back_callback_data="wiz:back:students",
                ),
            )
        await callback.answer()


@router.callback_query(
    StateFilter(LessonForm.schedule_dates_mode, LessonForm.date, LessonForm.weekdays, LessonForm.time, LessonForm.custom_time, LessonForm.start_date, LessonForm.end_date, LessonForm.reminders, LessonForm.confirm),
    F.data.startswith("wiz:back:"),
)
async def wizard_back(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    target = callback.data.split(":", 2)[2]
    data = await state.get_data()
    teacher_id = int(data.get("teacher_id", 0))
    students = db.list_students(teacher_id) if teacher_id else []
    selected_ids = set(data.get("selected_student_ids", []))

    if target == "students":
        await state.set_state(LessonForm.students)
        title = "Разовые занятия" if data.get("mode") == "single" else "Постоянное расписание"
        await callback.message.edit_text(
            f"{title}\n\n{wizard_progress(1, 5)}👥 Выберите одного или нескольких учеников:",
            reply_markup=with_wizard_nav(
                student_multiselect_keyboard(students, selected_ids),
                back_callback_data="wiz:exit",
            ),
        )
        await callback.answer()
        return

    if target == "schedule_dates_mode":
        await state.set_state(LessonForm.schedule_dates_mode)
        await callback.message.edit_text(
            f"{wizard_progress(2, 5)}Как задать занятия?\n\n"
            "🗓 По дням недели — создаёт постоянное расписание.\n"
            "📅 Конкретные даты — создаёт отдельные занятия на выбранные даты.",
            reply_markup=with_wizard_nav(
                schedule_dates_mode_keyboard(),
                back_callback_data="wiz:back:students",
            ),
        )
        await callback.answer()
        return

    if target == "date":
        today = datetime.now(local_zone(settings.timezone)).date()
        selected_dates = set(data.get("selected_dates", []))
        await state.set_state(LessonForm.date)
        await callback.message.edit_text(
            f"{wizard_progress(2, 5)}📅 Выберите даты:",
            reply_markup=with_wizard_nav(
                multiselect_calendar_keyboard(today.year, today.month, selected_dates),
                back_callback_data="wiz:back:students" if data.get("mode") == "single" else "wiz:back:schedule_dates_mode",
            ),
        )
        await callback.answer()
        return

    await callback.answer()


@router.callback_query(LessonForm.schedule_dates_mode, F.data.startswith("schedule_dates:"))
async def select_schedule_dates_mode(callback: CallbackQuery, state: FSMContext) -> None:
    mode = callback.data.split(":", 1)[1]
    if mode == "weekdays":
        await state.update_data(schedule_input_mode="weekdays", weekdays=[])
        await state.set_state(LessonForm.weekdays)
        await callback.message.edit_text(
            f"{wizard_progress(3, 5)}В какие дни проходят занятия?",
            reply_markup=with_wizard_nav(
                weekday_keyboard(set()),
                back_callback_data="wiz:back:schedule_dates_mode",
            ),
        )
        await callback.answer()
        return

    if mode == "custom":
        today = date.today()
        await state.update_data(schedule_input_mode="specific_dates", selected_dates=[])
        await state.set_state(LessonForm.date)
        await callback.message.edit_text(
            f"{wizard_progress(3, 5)}📅 Выберите конкретные даты занятий:",
            reply_markup=with_wizard_nav(
                multiselect_calendar_keyboard(today.year, today.month, set()),
                back_callback_data="wiz:back:schedule_dates_mode",
            ),
        )
        await callback.answer()
        return

    await callback.answer()


@router.callback_query(LessonForm.weekdays, F.data.startswith("repeat_days:"))
async def select_weekdays(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = set(data.get("weekdays", []))
    parts = callback.data.split(":")

    if parts[1] == "toggle":
        weekday = int(parts[2])
        if weekday in selected:
            selected.remove(weekday)
        else:
            selected.add(weekday)
        await state.update_data(weekdays=sorted(selected))
        await callback.message.edit_reply_markup(
            reply_markup=with_wizard_nav(
                weekday_keyboard(selected),
                back_callback_data="wiz:back:schedule_dates_mode",
            )
        )
        await callback.answer()
        return

    if parts[1] == "next":
        if not selected:
            await callback.answer("Выберите хотя бы один день недели.", show_alert=True)
            return
        await state.update_data(weekdays=sorted(selected))
        await state.set_state(LessonForm.time)
        await callback.message.edit_text(
            f"{wizard_progress(4, 5)}🕒 Выберите время:",
            reply_markup=with_wizard_nav(time_keyboard(), back_callback_data="wiz:back:weekdays"),
        )
    await callback.answer()


@router.callback_query(StateFilter(LessonForm.time), F.data == "wiz:back:weekdays")
async def back_to_weekdays(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = set(data.get("weekdays", []))
    await state.set_state(LessonForm.weekdays)
    await callback.message.edit_text(
        f"{wizard_progress(3, 5)}В какие дни проходят занятия?",
        reply_markup=with_wizard_nav(weekday_keyboard(selected), back_callback_data="wiz:back:schedule_dates_mode"),
    )
    await callback.answer()


@router.callback_query(LessonForm.date, F.data.startswith("dates:"))
async def select_single_dates(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    parts = callback.data.split(":")
    data = await state.get_data()
    selected_dates = set(data.get("selected_dates", []))

    if parts[1] == "month":
        await callback.message.edit_reply_markup(
            reply_markup=with_wizard_nav(
                multiselect_calendar_keyboard(int(parts[2]), int(parts[3]), selected_dates),
                back_callback_data="wiz:back:students",
            )
        )
        await callback.answer()
        return

    if parts[1] == "toggle":
        selected_date = date.fromisoformat(parts[2])
        if selected_date < datetime.now(local_zone(settings.timezone)).date():
            await callback.answer("Нельзя выбрать прошедшую дату.", show_alert=True)
            return

        selected_value = selected_date.isoformat()
        if selected_value in selected_dates:
            selected_dates.remove(selected_value)
        else:
            selected_dates.add(selected_value)

        await state.update_data(selected_dates=sorted(selected_dates))
        await callback.message.edit_reply_markup(
            reply_markup=with_wizard_nav(
                multiselect_calendar_keyboard(selected_date.year, selected_date.month, selected_dates),
                back_callback_data="wiz:back:students" if (await state.get_data()).get("mode") == "single" else "wiz:back:schedule_dates_mode",
            )
        )
        await callback.answer()
        return

    if parts[1] == "done":
        if not selected_dates:
            await callback.answer("Выберите хотя бы одну дату.", show_alert=True)
            return

        await state.update_data(selected_dates=sorted(selected_dates))
        data = await state.get_data()
        if data.get("return_to_confirm") is True:
            await state.update_data(return_to_confirm=False)
            reminders = list(data.get("reminders", ["day", "hour"]))
            await show_confirm_screen(callback, state, db, settings, reminders)
            await callback.answer()
            return
        await state.set_state(LessonForm.time)
        dates_text = ", ".join(
            format_date(date.fromisoformat(value))
            for value in sorted(selected_dates)
        )
        await callback.message.edit_text(
            f"{wizard_progress(4, 5)}📅 Даты: {dates_text}\n\n🕒 Выберите время:",
            reply_markup=with_wizard_nav(
                time_keyboard(),
                back_callback_data="wiz:back:date",
            ),
        )
        await callback.answer()
        return

    await callback.answer()


@router.callback_query(LessonForm.date, F.data.startswith("date:"))
async def select_single_date(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    parts = callback.data.split(":")
    if parts[1] == "month":
        data = await state.get_data()
        back_cb = "wiz:back:date"
        cancel_cb = "wiz:cancel"
        if data.get("mode") == "edit":
            lesson_id = int(data["lesson_id"])
            week_offset = int(data.get("week_offset", 0))
            back_cb = f"sch:lesson:edit:{lesson_id}:{week_offset}"
            cancel_cb = f"sch:lesson:{lesson_id}:{week_offset}"
        await callback.message.edit_reply_markup(
            reply_markup=with_wizard_nav(
                calendar_keyboard(int(parts[2]), int(parts[3]), prefix="date"),
                back_callback_data=back_cb,
                cancel_callback_data=cancel_cb,
            )
        )
        await callback.answer()
        return

    selected_date = date.fromisoformat(parts[2])
    if selected_date < datetime.now(local_zone(settings.timezone)).date():
        await callback.answer("Нельзя выбрать прошедшую дату.", show_alert=True)
        return

    await state.update_data(date=selected_date.isoformat())
    await state.set_state(LessonForm.time)
    data = await state.get_data()
    back_cb = "wiz:back:date"
    cancel_cb = "wiz:cancel"
    if data.get("mode") == "edit":
        lesson_id = int(data["lesson_id"])
        week_offset = int(data.get("week_offset", 0))
        back_cb = f"sch:lesson:edit:{lesson_id}:{week_offset}"
        cancel_cb = f"sch:lesson:{lesson_id}:{week_offset}"
    await callback.message.edit_text(
        f"📅 Дата: {selected_date:%d.%m.%Y}\n\n🕒 Выберите время:",
        reply_markup=with_wizard_nav(
            time_keyboard(),
            back_callback_data=back_cb,
            cancel_callback_data=cancel_cb,
        ),
    )
    await callback.answer()


@router.callback_query(LessonForm.time, F.data.startswith("lesson_time:"))
async def select_time(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    value = callback.data.split(":", 1)[1]
    if value == "other":
        await state.set_state(LessonForm.custom_time)
        await callback.message.edit_text(
            f"{wizard_progress(4, 5)}Введите время в формате ЧЧ:ММ, например 16:30.\n\n"
            "Можно отменить или вернуться назад кнопками ниже.",
            reply_markup=with_wizard_nav(
                InlineKeyboardMarkup(inline_keyboard=[]),
                back_callback_data="wiz:back:date",
            ),
        )
        await callback.answer()
        return

    lesson_time = time(hour=int(value[:2]), minute=int(value[2:]))
    await save_lesson_time(callback.message, state, db, settings, lesson_time)
    await callback.answer()


@router.message(LessonForm.custom_time)
async def custom_time(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    try:
        lesson_time = datetime.strptime((message.text or "").strip(), "%H:%M").time()
    except ValueError:
        await message.answer("Введите время в формате ЧЧ:ММ, например 16:30.")
        return
    await save_lesson_time(message, state, db, settings, lesson_time)


async def save_lesson_time(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    lesson_time: time,
) -> None:
    data = await state.get_data()
    await state.update_data(lesson_time=lesson_time.strftime("%H:%M"))

    if data["mode"] == "edit":
        zone = local_zone(settings.timezone)
        lesson_date = date.fromisoformat(data["date"])
        starts_local = datetime.combine(lesson_date, lesson_time, tzinfo=zone)
        if starts_local <= datetime.now(zone):
            await state.set_state(LessonForm.date)
            today = datetime.now(zone).date()
            lesson_id = int(data["lesson_id"])
            week_offset = int(data.get("week_offset", 0))
            await wizard_edit(
                state,
                message=message,
                text="Это время уже прошло. Выберите будущую дату:",
                reply_markup=with_wizard_nav(
                    calendar_keyboard(today.year, today.month, prefix="date"),
                    back_callback_data=f"sch:lesson:edit:{lesson_id}:{week_offset}",
                    cancel_callback_data=f"sch:lesson:{lesson_id}:{week_offset}",
                ),
            )
            return

        await state.update_data(starts_at=to_utc_iso(starts_local))
        await state.set_state(LessonForm.reminders)
        lesson_id = int(data["lesson_id"])
        week_offset = int(data.get("week_offset", 0))
        await wizard_edit(
            state,
            text=f"{wizard_progress(5, 5)}🔔 Выберите напоминания:",
            message=message,
            reply_markup=with_wizard_nav(
                reminder_keyboard(set(db.list_lesson_reminder_kinds(int(data["teacher_id"]), lesson_id) or {"day", "hour"})),
                back_callback_data=f"sch:lesson:edit:{lesson_id}:{week_offset}",
                cancel_callback_data=f"sch:lesson:{lesson_id}:{week_offset}",
            ),
        )
        return

    if data["mode"] == "single" or (
        data["mode"] == "regular" and data.get("schedule_input_mode") == "specific_dates"
    ):
        zone = local_zone(settings.timezone)
        lesson_dates = [date.fromisoformat(value) for value in data.get("selected_dates", [])]
        starts_values = [
            datetime.combine(lesson_date, lesson_time, tzinfo=zone)
            for lesson_date in lesson_dates
        ]
        if any(value <= datetime.now(zone) for value in starts_values):
            await state.set_state(LessonForm.date)
            today = datetime.now(zone).date()
            await message.answer(
                "Одна из выбранных дат уже прошла для этого времени. Выберите будущие даты:",
                reply_markup=multiselect_calendar_keyboard(
                    today.year,
                    today.month,
                    set(data.get("selected_dates", [])),
                ),
            )
            return

        await state.update_data(starts_at_values=[to_utc_iso(value) for value in starts_values])
        data = await state.get_data()
        if data.get("return_to_confirm") is True:
            await state.update_data(return_to_confirm=False)
            reminders = list(data.get("reminders", ["day", "hour"]))
            await state.set_state(LessonForm.confirm)
            await render_confirm_screen(message, state, db, settings, reminders)
            return
        await state.set_state(LessonForm.reminders)
        await wizard_edit(
            state,
            message=message,
            text=f"{wizard_progress(5, 5)}🔔 Выберите напоминания:",
            reply_markup=with_wizard_nav(reminder_keyboard({"day", "hour"}), back_callback_data="wiz:back:time"),
        )
        return

    await state.set_state(LessonForm.start_date)
    today = date.today()
    await message.answer(
        f"{wizard_progress(4, 5)}С какого числа действует расписание?",
        reply_markup=with_wizard_nav(
            calendar_keyboard(today.year, today.month, prefix="start"),
            back_callback_data="wiz:back:weekdays",
        ),
    )


@router.callback_query(LessonForm.start_date, F.data.startswith("start:"))
async def select_start_date(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    parts = callback.data.split(":")
    if parts[1] == "month":
        await callback.message.edit_reply_markup(
            reply_markup=with_wizard_nav(
                calendar_keyboard(int(parts[2]), int(parts[3]), prefix="start"),
                back_callback_data="wiz:back:weekdays",
            )
        )
        await callback.answer()
        return

    selected_date = date.fromisoformat(parts[2])
    if selected_date < datetime.now(local_zone(settings.timezone)).date():
        await callback.answer("Нельзя выбрать прошедшую дату.", show_alert=True)
        return

    await state.update_data(start_date=selected_date.isoformat())
    await state.set_state(LessonForm.end_date)
    await callback.message.edit_text(
        f"{wizard_progress(4, 5)}До какой даты создавать расписание?",
        reply_markup=with_wizard_nav(
            calendar_keyboard(selected_date.year, selected_date.month, prefix="end"),
            back_callback_data="wiz:back:start_date",
        ),
    )
    await callback.answer()


@router.callback_query(StateFilter(LessonForm.end_date), F.data == "wiz:back:start_date")
async def back_to_start_date(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    start_value = data.get("start_date")
    today = date.today()
    base = date.fromisoformat(start_value) if isinstance(start_value, str) else today
    await state.set_state(LessonForm.start_date)
    await callback.message.edit_text(
        f"{wizard_progress(4, 5)}С какого числа действует расписание?",
        reply_markup=with_wizard_nav(
            calendar_keyboard(base.year, base.month, prefix="start"),
            back_callback_data="wiz:back:weekdays",
        ),
    )
    await callback.answer()


@router.callback_query(LessonForm.end_date, F.data.startswith("end:"))
async def select_end_date(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if parts[1] == "month":
        await callback.message.edit_reply_markup(
            reply_markup=with_wizard_nav(
                calendar_keyboard(int(parts[2]), int(parts[3]), prefix="end"),
                back_callback_data="wiz:back:start_date",
            )
        )
        await callback.answer()
        return

    data = await state.get_data()
    end_date = date.fromisoformat(parts[2])
    start_date = date.fromisoformat(data["start_date"])
    if end_date < start_date:
        await callback.answer("Дата окончания должна быть не раньше даты начала.", show_alert=True)
        return

    occurrences = schedule_occurrences(
        start_date,
        end_date,
        data["weekdays"],
        time.fromisoformat(data["lesson_time"]),
    )
    if not occurrences:
        await callback.answer("В выбранном периоде нет занятий по этим дням.", show_alert=True)
        return
    if len(occurrences) > 104:
        await callback.answer("Выберите период поменьше: до 104 занятий.", show_alert=True)
        return

    await state.update_data(end_date=end_date.isoformat())
    await state.set_state(LessonForm.reminders)
    await callback.message.edit_text(
        f"{wizard_progress(5, 5)}🔔 Выберите напоминания:",
        reply_markup=with_wizard_nav(reminder_keyboard({"day", "hour"}), back_callback_data="wiz:back:end_date"),
    )
    await callback.answer()


@router.callback_query(StateFilter(LessonForm.reminders), F.data == "wiz:back:time")
async def back_to_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LessonForm.time)
    await callback.message.edit_text(
        f"{wizard_progress(4, 5)}🕒 Выберите время:",
        reply_markup=with_wizard_nav(time_keyboard(), back_callback_data="wiz:back:date"),
    )
    await callback.answer()


@router.callback_query(StateFilter(LessonForm.reminders), F.data == "wiz:back:end_date")
async def back_to_end_date(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    start_date = date.fromisoformat(data["start_date"])
    await state.set_state(LessonForm.end_date)
    await callback.message.edit_text(
        f"{wizard_progress(4, 5)}До какой даты создавать расписание?",
        reply_markup=with_wizard_nav(
            calendar_keyboard(start_date.year, start_date.month, prefix="end"),
            back_callback_data="wiz:back:start_date",
        ),
    )
    await callback.answer()


@router.callback_query(LessonForm.reminders, F.data.startswith("rem:"))
async def configure_reminders(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("reminders", ["day", "hour"]))

    if action == "save":
        if not selected:
            await callback.answer("Выберите хотя бы одно напоминание.", show_alert=True)
            return

        await state.update_data(reminders=sorted(selected))
        if data["mode"] == "edit":
            await update_lesson_from_form(callback, state, db, settings, sorted(selected))
            return

        await show_confirm_screen(callback, state, db, settings, sorted(selected))
        return

    if action in selected:
        selected.remove(action)
    else:
        selected.add(action)

    await state.update_data(reminders=sorted(selected))
    data = await state.get_data()
    if data.get("mode") == "edit":
        lesson_id = int(data["lesson_id"])
        week_offset = int(data.get("week_offset", 0))
        await callback.message.edit_reply_markup(
            reply_markup=with_wizard_nav(
                reminder_keyboard(selected),
                back_callback_data=f"sch:lesson:edit:{lesson_id}:{week_offset}",
                cancel_callback_data=f"sch:lesson:{lesson_id}:{week_offset}",
            )
        )
    else:
        await callback.message.edit_reply_markup(reply_markup=reminder_keyboard(selected))
    await callback.answer()


async def show_confirm_screen(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
    reminders: list[str],
) -> None:
    await render_confirm_screen(callback.message, state, db, settings, reminders)
    await callback.answer()


async def render_confirm_screen(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    reminders: list[str],
) -> None:
    data = await state.get_data()
    teacher_id = int(data["teacher_id"])
    student_ids = [int(value) for value in data["selected_student_ids"]]
    students_text = html.quote(selected_student_names(db, teacher_id, student_ids))

    if data["mode"] == "regular" and data.get("schedule_input_mode") != "specific_dates":
        start_date = date.fromisoformat(data["start_date"])
        end_date = date.fromisoformat(data["end_date"])
        lesson_time = time.fromisoformat(data["lesson_time"])
        days = ", ".join(WEEKDAYS_RU[int(day)] for day in data["weekdays"])
        occurrences = schedule_occurrences(start_date, end_date, data["weekdays"], lesson_time)
        preview_lines = [f"• {value:%d.%m.%Y %H:%M}" for value in occurrences[:6]]
        if len(occurrences) > 6:
            preview_lines.append(f"• ... и еще {len(occurrences) - 6}")

        await state.set_state(LessonForm.confirm)
        await message.edit_text(
            "📚 Проверьте расписание\n\n"
            f"👤 Учеников: {len(student_ids)}\n"
            f"{students_text}\n\n"
            f"🗓 Дни: {days}\n"
            f"🕒 Время: {lesson_time:%H:%M}\n"
            f"📅 Период: {start_date:%d.%m.%Y} — {end_date:%d.%m.%Y}\n"
            f"🔔 Напоминания: {reminders_human(reminders)}\n\n"
            "Ближайшие занятия:\n"
            f"{chr(10).join(preview_lines)}\n\n"
            f"Будет создано правил: {len(student_ids)}",
            reply_markup=lesson_confirm_keyboard(
                create_callback_data="conf:create:regular",
                back_callback_data="wiz:back:reminders",
            ),
        )
        return

    zone = local_zone(settings.timezone)
    selected_dates = [date.fromisoformat(value) for value in data.get("selected_dates", [])]
    lesson_time = time.fromisoformat(data["lesson_time"])
    starts_local = [datetime.combine(value, lesson_time, tzinfo=zone) for value in selected_dates]
    dates_text = ", ".join(format_date(value) for value in selected_dates)
    lessons_total = len(student_ids) * len(selected_dates)
    next_one = min(starts_local) if starts_local else None

    await state.set_state(LessonForm.confirm)
    next_text = (
        f"\n\nСледующее:\n📅 {next_one:%d.%m.%Y}\n🕒 {next_one:%H:%M}"
        if next_one is not None
        else ""
    )
    await message.edit_text(
        "📚 Проверьте занятия\n\n"
        f"👤 Учеников: {len(student_ids)}\n"
        f"{students_text}\n\n"
        f"📅 Даты: {dates_text}\n"
        f"🕒 Время: {lesson_time:%H:%M}\n"
        f"🔔 Напоминания: {reminders_human(reminders)}\n\n"
        f"Будет создано: {lessons_total} занятий"
        f"{next_text}",
        reply_markup=lesson_confirm_keyboard(
            create_callback_data="conf:create:single",
            back_callback_data="wiz:back:reminders",
        ),
    )


@router.callback_query(StateFilter(LessonForm.confirm), F.data.startswith("conf:edit:"))
async def confirm_edit(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    target = callback.data.split(":", 2)[2]
    data = await state.get_data()

    if target == "students":
        await state.update_data(return_to_confirm=True)
        await state.set_state(LessonForm.students)
        teacher_id = int(data["teacher_id"])
        students = db.list_students(teacher_id)
        selected_ids = set(data.get("selected_student_ids", []))
        title = "Разовые занятия" if data.get("mode") == "single" else "Постоянное расписание"
        await callback.message.edit_text(
            f"{title}\n\n{wizard_progress(1, 5)}👥 Выберите одного или нескольких учеников:",
            reply_markup=with_wizard_nav(
                student_multiselect_keyboard(students, selected_ids),
                back_callback_data="wiz:exit",
            ),
        )
        await callback.answer()
        return

    if target == "dates":
        await state.update_data(return_to_confirm=True)
        await state.set_state(LessonForm.date)
        today = datetime.now(local_zone(settings.timezone)).date()
        selected_dates = set(data.get("selected_dates", []))
        await callback.message.edit_text(
            f"{wizard_progress(2, 5)}📅 Выберите даты:",
            reply_markup=with_wizard_nav(
                multiselect_calendar_keyboard(today.year, today.month, selected_dates),
                back_callback_data="wiz:back:students" if data.get("mode") == "single" else "wiz:back:schedule_dates_mode",
            ),
        )
        await callback.answer()
        return

    if target == "time":
        await state.update_data(return_to_confirm=True)
        await state.set_state(LessonForm.time)
        await callback.message.edit_text(
            f"{wizard_progress(4, 5)}🕒 Выберите время:",
            reply_markup=with_wizard_nav(time_keyboard(), back_callback_data="wiz:back:date"),
        )
        await callback.answer()
        return

    if target == "reminders":
        await state.update_data(return_to_confirm=True)
        await state.set_state(LessonForm.reminders)
        selected = set(data.get("reminders", ["day", "hour"]))
        await callback.message.edit_text(
            f"{wizard_progress(5, 5)}🔔 Выберите напоминания:",
            reply_markup=with_wizard_nav(reminder_keyboard(selected), back_callback_data="wiz:back:time"),
        )
        await callback.answer()
        return

    await callback.answer()


@router.callback_query(StateFilter(LessonForm.confirm), F.data == "conf:create:single")
async def confirm_create_single(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    data = await state.get_data()
    reminders = list(data.get("reminders", ["day", "hour"]))
    await create_lessons_from_selected_dates(callback, state, db, settings, reminders)


@router.callback_query(StateFilter(LessonForm.confirm), F.data == "conf:create:regular")
async def confirm_create_regular(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    # reuse existing path
    await confirm_regular_schedule(callback, state, db)

async def update_lesson_from_form(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
    reminders: list[str],
) -> None:
    data = await state.get_data()
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return

    starts_at = parse_utc(data["starts_at"])
    reminders_count = db.update_lesson(
        teacher["id"],
        int(data["lesson_id"]),
        starts_at,
        reminders,
    )
    if reminders_count is None:
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    week_offset = data.get("week_offset")
    lesson_id = data.get("lesson_id")
    teacher_id = int(teacher["id"])
    await state.clear()
    if week_offset is not None and lesson_id is not None:
        await render_single_lesson_card(
            callback.message,
            db=db,
            settings=settings,
            teacher_id=teacher_id,
            lesson_id=int(lesson_id),
            week_offset=int(week_offset),
        )
        await callback.answer()
        return

    starts_local = starts_at.astimezone(local_zone(settings.timezone))
    await callback.message.edit_text(
        "✅ Занятие изменено\n\n"
        f"Ученик: {html.quote(data['student_name'])}\n"
        f"Когда: {starts_local:%d.%m.%Y %H:%M}\n"
        f"Напоминаний: {reminders_count}"
    )
    await callback.message.edit_text("Выберите действие:", reply_markup=home_inline_keyboard())
    await callback.answer()


async def create_lessons_from_selected_dates(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
    reminders: list[str],
) -> None:
    data = await state.get_data()
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return

    should_complete_setup = should_complete_setup_after_create(db, teacher["id"])
    student_ids = [int(value) for value in data["selected_student_ids"]]
    starts_at_values = [parse_utc(value) for value in data["starts_at_values"]]
    lessons_count, reminders_count = db.create_lessons(
        teacher["id"],
        student_ids,
        starts_at_values,
        reminders,
    )

    week_offset = data.get("week_offset")
    await state.clear()
    title = "✅ Занятия созданы" if lessons_count > 1 else "✅ Занятие создано"
    dates_text = ", ".join(
        format_date(value.astimezone(local_zone(settings.timezone)).date())
        for value in starts_at_values
    )
    await callback.message.edit_text(
        f"{title}\n\n"
        f"Ученики: {html.quote(selected_student_names(db, teacher['id'], student_ids))}\n"
        f"Даты: {dates_text}\n"
        f"Время: {data['lesson_time']}\n"
        f"Напоминаний: {reminders_count}"
    )
    complete_setup_if_needed(db, teacher["id"], should_complete_setup)
    if week_offset is not None:
        await render_schedule_week(
            callback.message,
            db,
            settings,
            teacher_id=int(teacher["id"]),
            week_offset=int(week_offset),
            edit_message_id=callback.message.message_id,
        )
    else:
        await callback.message.edit_text(
            (first_schedule_text() if should_complete_setup else "Выберите действие:"),
            reply_markup=home_inline_keyboard(),
        )
    await callback.answer()


async def show_regular_preview(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    reminders: list[str],
) -> None:
    data = await state.get_data()
    teacher_id = int(data["teacher_id"])
    student_ids = [int(value) for value in data["selected_student_ids"]]
    start_date = date.fromisoformat(data["start_date"])
    end_date = date.fromisoformat(data["end_date"])
    lesson_time = time.fromisoformat(data["lesson_time"])
    occurrences = schedule_occurrences(start_date, end_date, data["weekdays"], lesson_time)

    await state.update_data(reminders=reminders)
    await state.set_state(LessonForm.confirm)
    preview_lines = [f"• {value:%d.%m.%Y %H:%M}" for value in occurrences[:8]]
    if len(occurrences) > 8:
        preview_lines.append(f"• ... и еще {len(occurrences) - 8}")

    days = ", ".join(WEEKDAYS_RU[int(day)] for day in data["weekdays"])
    await callback.message.edit_text(
        "Проверьте постоянное расписание:\n\n"
        f"Ученики: {html.quote(selected_student_names(db, teacher_id, student_ids))}\n"
        f"Дни: {days}\n"
        f"Время: {lesson_time:%H:%M}\n"
        f"Период: {start_date:%d.%m.%Y} — {end_date:%d.%m.%Y}\n\n"
        "Ближайшие занятия:\n"
        f"{chr(10).join(preview_lines)}\n\n"
        f"Правил будет создано: {len(student_ids)}",
        reply_markup=repeat_preview_keyboard(),
    )
    await callback.answer()


@router.callback_query(LessonForm.confirm, F.data.startswith("repeat:"))
async def confirm_regular_schedule(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    action = callback.data.split(":", 1)[1]
    if action == "cancel":
        await state.clear()
        await callback.message.edit_text("Создание расписания отменено.\n\nВыберите действие:", reply_markup=home_inline_keyboard())
        await callback.answer()
        return

    data = await state.get_data()
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return

    should_complete_setup = should_complete_setup_after_create(db, teacher["id"])
    student_ids = [int(value) for value in data["selected_student_ids"]]
    rules_count = db.create_schedule_rules(
        teacher["id"],
        student_ids,
        [int(value) for value in data["weekdays"]],
        data["lesson_time"],
        data["start_date"],
        data["end_date"],
        data["reminders"],
    )

    await state.clear()
    await callback.message.edit_text(
        "✅ Постоянное расписание создано\n\n"
        f"Ученики: {html.quote(selected_student_names(db, teacher['id'], student_ids))}\n"
        f"Правил: {rules_count}"
    )
    complete_setup_if_needed(db, teacher["id"], should_complete_setup)
    await callback.message.answer(
        first_schedule_text() if should_complete_setup else "Выберите следующее действие.",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("student_del:"))
async def request_student_delete(callback: CallbackQuery, db: Database) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return

    student_id = int(callback.data.split(":", 1)[1])
    student = db.get_student_for_teacher(teacher["id"], student_id)
    if student is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    await callback.message.edit_text(
        "Удалить ученика?\n\n"
        f"Ученик: {html.quote(student['name'])}\n\n"
        "Все его занятия и расписания тоже будут удалены.",
        reply_markup=confirm_student_delete_keyboard(student_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("student_del_confirm:"))
async def confirm_student_delete(callback: CallbackQuery, db: Database) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return

    student_id = int(callback.data.split(":", 1)[1])
    student = db.get_student_for_teacher(teacher["id"], student_id)
    if student is None:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    if not db.delete_student(teacher["id"], student_id):
        await callback.answer("Ученик не найден", show_alert=True)
        return

    await callback.message.edit_text(f"🗑 Ученик удален: {html.quote(student['name'])}")
    await callback.answer()


@router.callback_query(F.data == "student_del_cancel")
async def cancel_student_delete(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Удаление ученика отменено.")
    await callback.answer()


@router.message(F.text == BTN_SCHEDULE)
async def show_schedule(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    teacher = await require_owner(message, db)
    if teacher is None:
        return
    await state.clear()
    await state.set_state(ScheduleScreen.view)
    ctx = await render_schedule_week(
        message,
        db,
        settings,
        teacher_id=int(teacher["id"]),
        week_offset=0,
        edit_message_id=None,
    )
    await state.update_data(screen_message_id=ctx.message_id, screen_chat_id=ctx.chat_id, week_offset=0)


async def render_schedule_week(
    message: Message,
    db: Database,
    settings: Settings,
    *,
    teacher_id: int,
    week_offset: int,
    edit_message_id: int | None = None,
) -> ScreenContext:
    start_local, end_local = week_bounds_local(settings.timezone, week_offset)
    start_utc = to_utc_iso(start_local)
    end_utc = to_utc_iso(end_local)
    zone = local_zone(settings.timezone)
    lessons_rows = db.list_lessons_between(teacher_id, start_utc, end_utc)
    lessons: list[dict] = [
        {
            "lesson_id": int(row["id"]),
            "student_name": str(row["student_name"]),
            "starts_local": parse_utc(str(row["starts_at"])).astimezone(zone),
        }
        for row in lessons_rows
    ]
    rules = db.list_active_schedule_rules(teacher_id)
    for rule in rules:
        rule_start = date.fromisoformat(str(rule["start_date"]))
        rule_end = date.fromisoformat(str(rule["end_date"])) if rule["end_date"] else None
        view_start = start_local.date()
        view_end = (end_local - timedelta(days=1)).date()
        if view_end < rule_start:
            continue
        if rule_end is not None and view_start > rule_end:
            continue

        effective_start = max(view_start, rule_start)
        effective_end = min(view_end, rule_end) if rule_end is not None else view_end
        weekdays = parse_int_csv(str(rule["weekdays"]))
        lesson_time = time.fromisoformat(str(rule["lesson_time"]))
        occurrences = schedule_occurrences(effective_start, effective_end, weekdays, lesson_time)
        for occ in occurrences:
            starts_local = datetime.combine(occ.date(), occ.time(), tzinfo=zone)
            lessons.append(
                {
                    "lesson_id": None,
                    "rule_id": int(rule["id"]),
                    "student_name": str(rule["student_name"]),
                    "starts_local": starts_local,
                }
            )

    lessons.sort(key=lambda x: x["starts_local"])

    text = schedule_week_text(week_title(start_local), lessons)
    markup = schedule_week_markup(week_offset, lessons)
    ctx = ScreenContext(chat_id=message.chat.id, message_id=edit_message_id) if edit_message_id is not None else None
    return await render_screen(message=message, ctx=ctx, text=text, reply_markup=markup)


@router.callback_query(StateFilter(ScheduleScreen.view), F.data.startswith("sch:week:"))
async def schedule_week_nav(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    week_offset = int(callback.data.split(":", 2)[2])
    data = await state.get_data()
    edit_message_id = int(data.get("screen_message_id") or callback.message.message_id)
    ctx = await render_schedule_week(
        callback.message,
        db,
        settings,
        teacher_id=int(teacher["id"]),
        week_offset=week_offset,
        edit_message_id=edit_message_id,
    )
    await state.update_data(screen_message_id=ctx.message_id, screen_chat_id=ctx.chat_id, week_offset=week_offset)
    await callback.answer()


@router.callback_query(StateFilter(ScheduleScreen.view), F.data == "sch:today")
async def schedule_today(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    data = await state.get_data()
    edit_message_id = int(data.get("screen_message_id") or callback.message.message_id)
    ctx = await render_schedule_week(
        callback.message,
        db,
        settings,
        teacher_id=int(teacher["id"]),
        week_offset=0,
        edit_message_id=edit_message_id,
    )
    await state.update_data(screen_message_id=ctx.message_id, screen_chat_id=ctx.chat_id, week_offset=0)
    await callback.answer()


@router.callback_query(StateFilter(ScheduleScreen.view), F.data.startswith("sch:lesson:"))
async def schedule_open_lesson(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    _p, _p2, lesson_id_str, week_offset_str = callback.data.split(":", 3)
    lesson_id = int(lesson_id_str)
    week_offset = int(week_offset_str)
    lesson = db.get_lesson_for_teacher(int(teacher["id"]), lesson_id)
    if lesson is None:
        await callback.answer("Занятие не найдено", show_alert=True)
        return
    starts_at = parse_utc(str(lesson["starts_at"])).astimezone(local_zone(settings.timezone))
    data = await state.get_data()
    edit_message_id = int(data.get("screen_message_id") or callback.message.message_id)
    ctx = await render_screen(
        message=callback.message,
        ctx=ScreenContext(chat_id=callback.message.chat.id, message_id=edit_message_id),
        text=(
            f"👤 {html.quote(str(lesson['student_name']))}\n\n"
            f"📅 {starts_at:%d.%m.%Y}\n"
            f"🕒 {starts_at:%H:%M}\n\n"
            "──────────────\n\n"
            "Выберите действие:"
        ),
        reply_markup=schedule_lesson_card_keyboard(
            lesson_id,
            week_offset=week_offset,
            back_callback_data=f"sch:week:{week_offset}",
        ),
    )
    await state.update_data(screen_message_id=ctx.message_id, screen_chat_id=ctx.chat_id, week_offset=week_offset)
    await callback.answer()


async def render_single_lesson_card(
    message: Message,
    *,
    db: Database,
    settings: Settings,
    teacher_id: int,
    lesson_id: int,
    week_offset: int,
) -> None:
    lesson = db.get_lesson_for_teacher(teacher_id, lesson_id)
    if lesson is None:
        await message.edit_text("Занятие не найдено.")
        return
    zone = local_zone(settings.timezone)
    starts_at = parse_utc(str(lesson["starts_at"])).astimezone(zone)
    reminder_kinds = db.list_lesson_reminder_kinds(teacher_id, lesson_id) or []
    reminders_lines = "\n".join(f"🔔 {REMINDER_OPTIONS[k].label}" for k in reminder_kinds if k in REMINDER_OPTIONS)
    reminders_block = f"\n\n{reminders_lines}" if reminders_lines else "\n\n🔕 Без напоминаний"
    await message.edit_text(
        "📖 Занятие\n\n"
        f"👤 {html.quote(str(lesson['student_name']))}\n\n"
        f"📅 {starts_at:%d.%m.%Y}\n"
        f"🕒 {starts_at:%H:%M}"
        f"{reminders_block}\n\n"
        "──────────────\n\n"
        "Выберите действие:",
        reply_markup=schedule_lesson_card_keyboard(
            lesson_id,
            week_offset=week_offset,
            back_callback_data=f"sch:week:{week_offset}",
        ),
    )


@router.callback_query(StateFilter(None), F.data.startswith("sch:lesson:edit:"))
async def schedule_lesson_edit_menu(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    _p, _p2, _p3, lesson_id_str, week_offset_str = callback.data.split(":", 4)
    lesson_id = int(lesson_id_str)
    week_offset = int(week_offset_str)
    await state.clear()
    await state.update_data(
        mode="edit",
        teacher_id=int(teacher["id"]),
        lesson_id=lesson_id,
        week_offset=week_offset,
        screen_message_id=callback.message.message_id,
        screen_chat_id=callback.message.chat.id,
    )
    await state.set_state(LessonForm.confirm)
    await callback.message.edit_text(
        "Что изменить?",
        reply_markup=lesson_edit_menu_keyboard(lesson_id=lesson_id, week_offset=week_offset),
    )
    await callback.answer()


@router.callback_query(StateFilter(None), F.data.startswith("sch:lesson:delete:"))
async def schedule_lesson_delete_from_card(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    _p, _p2, _p3, lesson_id_str, week_offset_str = callback.data.split(":", 4)
    lesson_id = int(lesson_id_str)
    week_offset = int(week_offset_str)
    if not db.delete_lesson(int(teacher["id"]), lesson_id):
        await callback.answer("Занятие не найдено", show_alert=True)
        return
    await callback.answer("Занятие удалено")
    await render_schedule_week(
        callback.message,
        db,
        settings,
        teacher_id=int(teacher["id"]),
        week_offset=week_offset,
        edit_message_id=callback.message.message_id,
    )


@router.callback_query(StateFilter(None), F.data.startswith("sch:lesson:field:"))
async def schedule_lesson_edit_field(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    _p, _p2, _p3, field, lesson_id_str, week_offset_str = callback.data.split(":", 5)
    lesson_id = int(lesson_id_str)
    week_offset = int(week_offset_str)
    data = await state.get_data()
    if data.get("mode") != "edit":
        await state.clear()
        await state.update_data(
            mode="edit",
            teacher_id=int(teacher["id"]),
            lesson_id=lesson_id,
            week_offset=week_offset,
            screen_message_id=callback.message.message_id,
            screen_chat_id=callback.message.chat.id,
        )
    await state.update_data(return_to_confirm=True)

    if field == "date":
        await state.set_state(LessonForm.date)
        today = datetime.now(local_zone(settings.timezone)).date()
        await callback.message.edit_text(
            "📅 Выберите новую дату:",
            reply_markup=with_wizard_nav(
                calendar_keyboard(today.year, today.month, prefix="date"),
                back_callback_data=f"sch:lesson:edit:{lesson_id}:{week_offset}",
            ),
        )
        await callback.answer()
        return

    if field == "time":
        await state.set_state(LessonForm.time)
        await callback.message.edit_text(
            "🕒 Выберите новое время:",
            reply_markup=with_wizard_nav(
                time_keyboard(),
                back_callback_data=f"sch:lesson:edit:{lesson_id}:{week_offset}",
            ),
        )
        await callback.answer()
        return

    if field == "reminders":
        await state.set_state(LessonForm.reminders)
        kinds = db.list_lesson_reminder_kinds(int(teacher["id"]), lesson_id) or ["day", "hour"]
        await state.update_data(reminders=sorted(set(kinds)))
        await callback.message.edit_text(
            "🔔 Выберите напоминания:",
            reply_markup=with_wizard_nav(
                reminder_keyboard(set(kinds)),
                back_callback_data=f"sch:lesson:edit:{lesson_id}:{week_offset}",
            ),
        )
        await callback.answer()
        return

    await callback.answer()


@router.callback_query(
    StateFilter(
        LessonForm.date,
        LessonForm.time,
        LessonForm.custom_time,
        LessonForm.reminders,
        LessonForm.confirm,
    ),
    F.data.startswith("sch:lesson:"),
)
async def schedule_back_to_lesson_card_from_flow(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer()
        return
    # sch:lesson:<lesson_id>:<week_offset>
    if parts[0] != "sch" or parts[1] != "lesson":
        await callback.answer()
        return
    try:
        lesson_id = int(parts[2])
        week_offset = int(parts[3])
    except ValueError:
        await callback.answer()
        return

    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    await state.clear()
    await render_single_lesson_card(
        callback.message,
        db=db,
        settings=settings,
        teacher_id=int(teacher["id"]),
        lesson_id=lesson_id,
        week_offset=week_offset,
    )
    await callback.answer()


@router.callback_query(StateFilter(None), F.data.startswith("sch:occ:"))
async def schedule_open_occurrence(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    _p, _p2, rule_id_str, occ_date_str, week_offset_str = callback.data.split(":", 4)
    rule_id = int(rule_id_str)
    week_offset = int(week_offset_str)
    rule = db.get_schedule_rule_for_teacher(int(teacher["id"]), rule_id)
    if rule is None:
        await callback.answer("Правило не найдено", show_alert=True)
        return
    occ_date = date.fromisoformat(occ_date_str)
    occ_time = time.fromisoformat(str(rule["lesson_time"]))
    zone = local_zone(settings.timezone)
    starts_local = datetime.combine(occ_date, occ_time, tzinfo=zone)
    days = ", ".join(WEEKDAYS_RU[int(v)] for v in parse_int_csv(str(rule["weekdays"])))
    await callback.message.edit_text(
        f"👤 {html.quote(str(rule['student_name']))}\n\n"
        f"🔁 Постоянное расписание\n"
        f"🗓 Дни: {days}\n"
        f"🕒 Время: {occ_time:%H:%M}\n\n"
        f"📅 Экземпляр: {starts_local:%d.%m.%Y %H:%M}\n\n"
        "──────────────\n\n"
        "Выберите действие:",
        reply_markup=schedule_occurrence_card_keyboard(
            pause_callback_data=f"sch:rule:pause:{rule_id}:{week_offset}",
            delete_callback_data=f"sch:rule:delete:{rule_id}:{week_offset}",
            back_callback_data=f"sch:week:{week_offset}",
        ),
    )
    await callback.answer()


@router.callback_query(StateFilter(None), F.data.startswith("sch:rule:pause:"))
async def schedule_pause_rule(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    _p, _p2, _p3, rule_id_str, week_offset_str = callback.data.split(":", 4)
    rule_id = int(rule_id_str)
    week_offset = int(week_offset_str)
    if not db.deactivate_schedule_rule(int(teacher["id"]), rule_id):
        await callback.answer("Правило не найдено", show_alert=True)
        return
    await callback.answer("Правило отключено")
    await render_schedule_week(
        callback.message,
        db,
        settings,
        teacher_id=int(teacher["id"]),
        week_offset=week_offset,
        edit_message_id=callback.message.message_id,
    )


@router.callback_query(StateFilter(None), F.data.startswith("sch:rule:delete:"))
async def schedule_delete_rule(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return
    _p, _p2, _p3, rule_id_str, week_offset_str = callback.data.split(":", 4)
    rule_id = int(rule_id_str)
    week_offset = int(week_offset_str)
    if not db.delete_schedule_rule(int(teacher["id"]), rule_id):
        await callback.answer("Правило не найдено", show_alert=True)
        return
    await callback.answer("Правило удалено")
    await render_schedule_week(
        callback.message,
        db,
        settings,
        teacher_id=int(teacher["id"]),
        week_offset=week_offset,
        edit_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith("lesson_del:"))
async def delete_lesson(callback: CallbackQuery, db: Database) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return

    lesson_id = int(callback.data.split(":", 1)[1])
    if not db.delete_lesson(teacher["id"], lesson_id):
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    await callback.message.edit_text("🗑 Занятие удалено.")
    await callback.answer()


@router.callback_query(F.data.startswith("lesson_edit:"))
async def edit_lesson(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    teacher = db.get_teacher_by_telegram(callback.from_user.id)
    if teacher is None:
        await callback.answer("Сначала откройте /start", show_alert=True)
        return

    lesson_id = int(callback.data.split(":", 1)[1])
    lesson = db.get_lesson_for_teacher(teacher["id"], lesson_id)
    if lesson is None:
        await callback.answer("Занятие не найдено", show_alert=True)
        return

    await state.clear()
    await state.update_data(
        mode="edit",
        lesson_id=lesson_id,
        student_name=lesson["student_name"],
    )
    await state.set_state(LessonForm.date)
    today = datetime.now(local_zone(settings.timezone)).date()
    await callback.message.edit_text(
        f"Изменяем занятие: {html.quote(lesson['student_name'])}\n\n"
        "📅 Выберите новую дату:",
        reply_markup=calendar_keyboard(today.year, today.month, prefix="date"),
    )
    await callback.answer()


@router.message(F.text == BTN_SETTINGS)
async def settings(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    teacher = await require_owner(message, db)
    if teacher is None:
        return
    await message.answer(
        "⚙ Настройки\n\n"
        f"Часовой пояс: {html.quote(settings.timezone)}\n"
        "Напоминания по умолчанию: за день и за час."
    )


@router.message(LessonForm.students)
@router.message(LessonForm.schedule_dates_mode)
@router.message(LessonForm.date)
@router.message(LessonForm.weekdays)
@router.message(LessonForm.time)
@router.message(LessonForm.custom_time)
@router.message(LessonForm.start_date)
@router.message(LessonForm.end_date)
@router.message(LessonForm.reminders)
@router.message(LessonForm.confirm)
async def lesson_flow_text_fallback(message: Message) -> None:
    await message.answer("Сейчас идет настройка занятия. Используйте кнопки выше или нажмите ❌ Отмена.")


@router.message(StateFilter(None))
async def fallback(message: Message, db: Database) -> None:
    teacher = db.get_teacher_by_telegram(message.from_user.id)
    if teacher is not None:
        await message.answer("Выберите действие в меню.", reply_markup=main_menu())
        return

    if db.is_student(message.from_user.id):
        await message.answer("Вы подключены как ученик. Здесь будут приходить напоминания.")
        return

    await message.answer("Нажмите /start, чтобы начать.")


def selected_student_names(db: Database, teacher_id: int, student_ids: list[int]) -> str:
    students = db.list_students_by_ids(teacher_id, student_ids)
    return ", ".join(student["name"] for student in students)


def should_complete_setup_after_create(db: Database, teacher_id: int) -> bool:
    return not db.is_setup_completed(teacher_id) and not db.has_any_schedule(teacher_id)


def complete_setup_if_needed(db: Database, teacher_id: int, should_complete: bool) -> None:
    if should_complete:
        db.mark_setup_completed(teacher_id)


def first_schedule_text() -> str:
    return (
        "🟩🟩🟩\n\n"
        "🎉 Всё готово!\n\n"
        "Первое занятие уже в расписании.\n\n"
        "Теперь бот будет автоматически напоминать ученикам о предстоящих уроках.\n\n"
        "Желаем продуктивных занятий!"
    )


def schedule_occurrences(
    start_date: date,
    end_date: date,
    weekdays: list[int],
    lesson_time: time,
) -> list[datetime]:
    selected = set(int(value) for value in weekdays)
    current = start_date
    values = []
    while current <= end_date:
        if current.weekday() in selected:
            values.append(datetime.combine(current, lesson_time))
        current += timedelta(days=1)
    return values


def parse_int_csv(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item]


def format_date(value: date) -> str:
    return f"{value:%d.%m.%Y}"


def week_bounds_local(zone_name: str, week_offset: int) -> tuple[datetime, datetime]:
    zone = local_zone(zone_name)
    today = datetime.now(zone).date()
    monday = today - timedelta(days=today.weekday())
    start = datetime.combine(monday + timedelta(days=week_offset * 7), time(0, 0), tzinfo=zone)
    end = start + timedelta(days=7)
    return start, end


def week_title(start_local: datetime) -> str:
    end_local = (start_local + timedelta(days=6)).date()
    return f"Неделя {start_local:%d.%m}–{end_local:%d.%m}"


def schedule_week_text(week_label: str, lessons: list[dict]) -> str:
    lines: list[str] = ["📖 Расписание", "", f"🗓 {week_label}", "", "────────────────"]
    if not lessons:
        lines.append("")
        lines.append("Пока нет занятий на этой неделе.")
        return "\n".join(lines)

    current_day: date | None = None
    for item in lessons:
        starts_local: datetime = item["starts_local"]
        if current_day != starts_local.date():
            current_day = starts_local.date()
            lines.append("")
            DAYS_MAP = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"}
            day_en = f"{current_day:%a}"
            day_ru = DAYS_MAP.get(day_en, day_en)
            lines.append(f"📅 {day_ru} {current_day:%d.%m}")
        lines.append(f"{starts_local:%H:%M} • {html.quote(item['student_name'])}")
    return "\n".join(lines)


def schedule_week_markup(week_offset: int, lessons: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in lessons[:25]:
        starts_local: datetime = item["starts_local"]
        if item["lesson_id"] is None:
            rule_id = int(item["rule_id"])
            occ_date = starts_local.date().isoformat()
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{starts_local:%d.%m %H:%M} • {item['student_name']} (🔁)",
                        callback_data=f"sch:occ:{rule_id}:{occ_date}:{week_offset}",
                    )
                ]
            )
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{starts_local:%d.%m %H:%M} • {item['student_name']}",
                    callback_data=f"sch:lesson:{item['lesson_id']}:{week_offset}",
                )
            ]
        )
    base = schedule_week_keyboard(week_offset).inline_keyboard
    rows.extend(base)
    return InlineKeyboardMarkup(inline_keyboard=rows)
