import asyncio
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from bot.handlers.admin import router as admin_router
from bot.handlers.user import router as user_router
from bot.middlewares import AuthMiddleware
from config import settings
from db.database import db
from parser.wb_parser import WBParser
from scheduler.jobs import check_prices, cleanup_old_history


async def main() -> None:
    # ---------------------------------------------------------------- logging
    logger.add(
        "logs/tracker.log",
        rotation="10 MB",
        retention="7 days",
        format="{time} | {level} | {name} | {message}",
        level=settings.LOG_LEVEL,
        encoding="utf-8",
    )

    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)

    # ----------------------------------------------------------------- parser
    async with WBParser() as parser:
        bot = Bot(token=settings.BOT_TOKEN)
        dp = Dispatcher()

        # Inject parser as a named dependency available in all handlers
        # (handlers declare `parser: WBParser` in their signature)
        dp["parser"] = parser

        # --------------------------------------------------------- middleware
        # Registered per event-type so `event` has the correct concrete type
        # inside AuthMiddleware (Message / CallbackQuery instanceof checks work).
        dp.message.outer_middleware(AuthMiddleware())
        dp.callback_query.outer_middleware(AuthMiddleware())

        # ----------------------------------------------------------- routers
        # Admin router first — its IsAdmin filter makes it more specific
        dp.include_router(admin_router)
        dp.include_router(user_router)

        # ------------------------------------------------------ error handler
        async def on_error(event: ErrorEvent, bot: Bot) -> None:
            logger.error(
                f"Unhandled exception: {event.exception}",
                exc_info=event.exception,
            )
            if settings.ADMIN_IDS:
                try:
                    await bot.send_message(
                        settings.ADMIN_IDS[0],
                        f"❌ Unhandled error:\n"
                        f"<code>{type(event.exception).__name__}: {event.exception}</code>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass  # Never let the error handler raise

        dp.errors.register(on_error)

        # --------------------------------------------------------------- jobs
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            check_prices,
            "interval",
            hours=settings.CHECK_INTERVAL_HOURS,
            id="check_prices",
            kwargs={"bot": bot, "parser": parser},
        )
        scheduler.add_job(
            cleanup_old_history,
            "cron",
            hour=4,
            minute=0,
            id="cleanup_history",
        )

        # ---------------------------------------------------------- lifecycle
        async def on_startup() -> None:
            await db.init()
            scheduler.start()
            logger.info("Bot started")

        async def on_shutdown() -> None:
            scheduler.shutdown(wait=False)
            await db.close()
            logger.info("Bot stopped")

        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)

        # Start polling — pass only the update types our handlers actually use
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )


if __name__ == "__main__":
    asyncio.run(main())
