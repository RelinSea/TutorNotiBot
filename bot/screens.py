from __future__ import annotations

from dataclasses import dataclass

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


@dataclass(frozen=True)
class ScreenContext:
    chat_id: int
    message_id: int


async def render_screen(
    *,
    message: Message,
    ctx: ScreenContext | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> ScreenContext:
    if ctx is not None:
        try:
            await message.bot.edit_message_text(
                chat_id=ctx.chat_id,
                message_id=ctx.message_id,
                text=text,
                reply_markup=reply_markup,
            )
            return ctx
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return ctx
        except Exception:
            pass

    sent = await message.answer(text, reply_markup=reply_markup)
    return ScreenContext(chat_id=sent.chat.id, message_id=sent.message_id)

