from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

_PAGE_SIZE = 10


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Main reply keyboard shown after /start."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="➕ Добавить товар")
    builder.button(text="📋 Мои товары")
    builder.button(text="📊 Статистика")
    builder.button(text="❌ Отмена")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def products_list_kb(products: list[dict], page: int) -> InlineKeyboardMarkup:
    """Pagination keyboard for /list.

    Shows ◀️ / ▶️ navigation buttons when there are multiple pages.
    callback_data format: list_page:{page}
    """
    builder = InlineKeyboardBuilder()
    total = len(products)

    if page > 0:
        builder.button(text="◀️ Назад", callback_data=f"list_page:{page - 1}")
    if (page + 1) * _PAGE_SIZE < total:
        builder.button(text="▶️ Вперёд", callback_data=f"list_page:{page + 1}")

    # If both buttons present, put them side by side; otherwise just one row
    if page > 0 and (page + 1) * _PAGE_SIZE < total:
        builder.adjust(2)

    return builder.as_markup()


def delete_confirm_kb(product_id: int) -> InlineKeyboardMarkup:
    """Confirmation keyboard for /delete.

    callback_data: delete_confirm:{id} | delete_cancel
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"delete_confirm:{product_id}")
    builder.button(text="❌ Отмена", callback_data="delete_cancel")
    builder.adjust(2)
    return builder.as_markup()


def product_actions_kb(product_id: int, is_paused: bool) -> InlineKeyboardMarkup:
    """Per-product action keyboard: pause/resume + delete.

    callback_data: pause:{id} | resume:{id} | delete:{id}
    """
    builder = InlineKeyboardBuilder()
    if is_paused:
        builder.button(text="▶️ Возобновить", callback_data=f"resume:{product_id}")
    else:
        builder.button(text="⏸ Пауза", callback_data=f"pause:{product_id}")
    builder.button(text="🗑 Удалить", callback_data=f"delete:{product_id}")
    builder.adjust(2)
    return builder.as_markup()


def open_wb_kb(url: str) -> InlineKeyboardMarkup:
    """Inline keyboard with a single 'Open on WB' URL button.

    Used in price-change notifications from the scheduler.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 Открыть на WB", url=url)
    return builder.as_markup()
