import logging

from aiogram import Bot, Router, html
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.database import Database
from bot.keyboards import create_lesson_keyboard
from bot.utils import user_name


LOG = logging.getLogger(__name__)
router = Router(name=__name__)


@router.message(CommandStart(deep_link=True))
async def invite_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    db: Database,
    bot: Bot,
) -> None:
    await state.clear()
    code = command.args.strip() if command.args else ""
    student_name = user_name(message)
    status, invite = db.accept_invite(code, message.from_user.id, student_name)

    if status == "missing":
        await message.answer("Ссылка устарела или неверна. Попросите репетитора создать новую.")
        return
    if status == "self":
        await message.answer("Нельзя использовать собственную ссылку приглашения.")
        return
    if status == "used":
        await message.answer("Эту ссылку уже использовали. Попросите репетитора создать новую.")
        return

    await message.answer(
        "👋 Добро пожаловать!\n\n"
        f"Вы успешно подключились к репетитору: {html.quote(invite['teacher_name'])}.\n\n"
        "Теперь вы будете получать напоминания о занятиях здесь."
    )

    if invite["used_at"] is None:
        try:
            await bot.send_message(
                invite["teacher_telegram_id"],
                f"🎉 {html.quote(student_name)} готов к занятиям.\n\n"
                "🟩🟩⬜\n\n"
                "Выберите, что создать:",
                reply_markup=create_lesson_keyboard(),
            )
        except TelegramAPIError:
            LOG.exception(
                "Failed to notify teacher %s about connected student %s",
                invite["teacher_telegram_id"],
                message.from_user.id,
            )
