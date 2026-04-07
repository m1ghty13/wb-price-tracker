from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import settings
from db.database import db


class AuthMiddleware(BaseMiddleware):
    """Access control middleware.

    - ALLOWED_USERS empty  → open bot, all users pass.
    - ALLOWED_USERS set    → only listed user_ids pass; others get a polite refusal.

    upsert_user is called AFTER the ALLOWED_USERS check so unauthorized
    users are never written to the database.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")

        # No user attached to this update (e.g. channel post) — skip check
        if user is None:
            return await handler(event, data)

        allowed = settings.ALLOWED_USERS
        if allowed and user.id not in allowed:
            if isinstance(event, Message):
                await event.answer("⛔ У вас нет доступа к этому боту.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Нет доступа", show_alert=True)
            return  # do not call handler

        # Only authorized users are upserted into the database
        await db.upsert_user(user.id, user.username)
        return await handler(event, data)
