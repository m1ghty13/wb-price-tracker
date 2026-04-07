import asyncio

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot.filters import IsAdmin
from bot.states import BroadcastStates
from db.database import db

router = Router()

# All handlers in this router require admin — silent ignore for non-admins
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ============================================================= /admin_stats


@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message) -> None:
    stats = await db.get_db_stats()
    text = (
        "<b>📊 Admin Stats</b>\n\n"
        f"👤 Пользователей (активных): {stats['total_users']}\n"
        f"🛍 Товаров в мониторинге: {stats['total_products']}\n"
        f"🔔 Изменений цен за 24ч: {stats['checks_24h']}"
    )
    await message.answer(text, parse_mode="HTML")


# ============================================================== /broadcast


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.waiting_for_message)
    await message.answer(
        "Отправьте сообщение для рассылки.\n"
        "Поддерживаются текст, фото, видео, документы.\n\n"
        "/cancel — отменить"
    )


@router.message(BroadcastStates.waiting_for_message)
async def process_broadcast_message(message: Message, state: FSMContext) -> None:
    # Save source message coordinates — will be copied via copy_message
    await state.update_data(
        broadcast_message_id=message.message_id,
        broadcast_chat_id=message.chat.id,
    )

    users = await db.get_all_active_users()
    count = len(users)

    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"✅ Разослать {count} пользователям",
        callback_data="broadcast_confirm",
    )
    builder.button(text="❌ Отмена", callback_data="broadcast_cancel")
    builder.adjust(1)

    await message.answer(
        f"Предпросмотр сообщения выше.\n"
        f"Разослать <b>{count}</b> активным пользователям?",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "broadcast_confirm")
async def cb_broadcast_confirm(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    await state.clear()

    source_message_id = data.get("broadcast_message_id")
    source_chat_id = data.get("broadcast_chat_id")

    if not source_message_id:
        await callback.message.edit_text("Ошибка: исходное сообщение не найдено.")
        await callback.answer()
        return

    users = await db.get_all_active_users()
    await callback.message.edit_text(
        f"⏳ Рассылка запущена... ({len(users)} пользователей)"
    )
    await callback.answer()

    log = logger.bind(job="broadcast")
    sent = 0
    failed = 0

    for user in users:
        try:
            await bot.copy_message(
                chat_id=user["user_id"],
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
            sent += 1
        except TelegramForbiddenError:
            # Bot was blocked — deactivate user silently
            await db.set_user_inactive(user["user_id"])
            failed += 1
        except Exception as exc:
            log.error(f"Failed to send to user {user['user_id']}: {exc}")
            failed += 1
        await asyncio.sleep(0.05)  # 50 ms rate-limit between sends

    await callback.message.answer(
        f"✅ Рассылка завершена.\n"
        f"Отправлено: {sent}\n"
        f"Не доставлено: {failed}"
    )


@router.callback_query(F.data == "broadcast_cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Рассылка отменена.")
    await callback.answer()
