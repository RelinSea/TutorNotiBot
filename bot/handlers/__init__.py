from bot.handlers.errors import router as errors_router
from bot.handlers.student import router as student_router
from bot.handlers.teacher import router as teacher_router


__all__ = ["errors_router", "student_router", "teacher_router"]
