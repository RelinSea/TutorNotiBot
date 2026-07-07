import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import Settings
from bot.database import Database
from bot.handlers import errors_router, student_router, teacher_router
from bot.scheduler import reminder_worker


LOG = logging.getLogger(__name__)


async def run_polling_forever(dp: Dispatcher, bot: Bot) -> None:
    delay_seconds = 1.0
    max_delay_seconds = 60.0
    while True:
        try:
            LOG.info("Starting bot polling")
            await dp.start_polling(bot)
            delay_seconds = 1.0
        except TelegramNetworkError as exc:
            # DNS fail / disconnect / temporary network issues.
            LOG.error("Polling network error: %s", exc)
        except (OSError, asyncio.TimeoutError) as exc:
            # aiohttp connector DNS errors often surface as OSError.
            LOG.error("Polling transport error: %s", exc)
        except asyncio.CancelledError:
            raise

        LOG.warning("Retry polling in %.1f seconds", delay_seconds)
        await asyncio.sleep(delay_seconds)
        delay_seconds = min(delay_seconds * 2.0, max_delay_seconds)


async def main() -> None:
    settings = Settings.from_env()
    db = Database(settings.database)
    db.init()

    session = None
    if settings.proxy:
        from aiogram.client.session.aiohttp import AiohttpSession
        LOG.info("Using proxy: %s", settings.proxy)
        session = AiohttpSession(proxy=settings.proxy)

    bot = Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage(), db=db, settings=settings)
    dp.include_router(errors_router)
    dp.include_router(student_router)
    dp.include_router(teacher_router)

    worker = asyncio.create_task(reminder_worker(bot, db, settings))
    try:
        await run_polling_forever(dp, bot)
    except (KeyboardInterrupt, SystemExit):
        # Allow clean shutdown on Ctrl+C.
        pass
    finally:
        LOG.info("Stopping bot")
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(main())
