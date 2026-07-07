import logging

from aiogram import Router
from aiogram.types import ErrorEvent


LOG = logging.getLogger(__name__)
router = Router(name=__name__)


@router.errors()
async def errors_handler(event: ErrorEvent) -> None:
    LOG.exception("Unhandled update processing error", exc_info=event.exception)
