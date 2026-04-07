import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from loguru import logger

from bot.keyboards import open_wb_kb
from db.database import db
from parser.wb_parser import ProductNotFoundError, WBParser, WBParserError
from utils.formatters import format_price_change


async def _notify(bot: Bot, user_id: int, text: str, **kwargs) -> None:
    """Send a notification. Silently deactivates user on TelegramForbiddenError."""
    try:
        await bot.send_message(user_id, text, **kwargs)
    except TelegramForbiddenError:
        await db.set_user_inactive(user_id)


async def check_prices(bot: Bot, parser: WBParser) -> None:
    """Check prices for all active (non-paused, available) products.

    - Price changed   → update DB + notify user
    - Price unchanged → touch last_checked only
    - ProductNotFound → mark unavailable + notify user once
    - Any other error → log and continue (never breaks the whole job)
    """
    log = logger.bind(job="check_prices")
    products = await db.get_all_active_products()
    log.info(f"Starting price check for {len(products)} products")

    for i, product in enumerate(products):
        if i > 0:
            await asyncio.sleep(0.5)  # rate-limit between WB requests

        pid = product["id"]
        try:
            info = await parser.parse_article(product["wb_article"])

            if info.current_price != product["current_price"]:
                old_price = product["current_price"]
                await db.update_price(pid, info.current_price)
                log.info(
                    f"Price changed for product {pid}: "
                    f"{old_price} → {info.current_price}"
                )
                await _notify(
                    bot,
                    product["user_id"],
                    format_price_change(product, old_price, info.current_price),
                    reply_markup=open_wb_kb(product["url"]),
                )
            else:
                # Price unchanged — only update the timestamp
                await db.update_last_checked(pid)

        except ProductNotFoundError:
            log.warning(f"Product {pid} (article {product['wb_article']}) not found on WB")
            await db.set_unavailable(pid)
            await _notify(
                bot,
                product["user_id"],
                f"⚠️ Товар «{product['name']}» недоступен на WB. "
                f"Мониторинг приостановлен. Возобновить: /resume {pid}",
            )

        except WBParserError as exc:
            log.warning(f"Parser error for product {pid}: {exc}")

        except Exception:
            log.exception(f"Unexpected error processing product {pid}")

    log.info("Price check finished")


async def cleanup_old_history() -> None:
    """Delete price_history records older than 90 days.

    Runs daily at 04:00 UTC.
    """
    log = logger.bind(job="cleanup_history")
    rowcount = await db.cleanup_history()
    log.info(f"Deleted {rowcount} old price history records")
