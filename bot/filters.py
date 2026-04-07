from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from config import settings


class IsAdmin(Filter):
    """Pass only if the event sender is in ADMIN_IDS.

    Non-admin events are silently ignored (handler not called).
    Works for both Message and CallbackQuery events.
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = getattr(event, "from_user", None)
        if user is None:
            return False
        return user.id in settings.ADMIN_IDS
