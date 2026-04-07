from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot.keyboards import (
    delete_confirm_kb,
    main_menu_kb,
    open_wb_kb,
    product_actions_kb,
    products_list_kb,
)
from bot.states import AddProductStates
from config import settings
from db.database import db
from parser.wb_parser import ProductNotFoundError, WBParser, WBParserError
from utils.formatters import (
    _truncate,
    format_price_change,
    format_product_added,
    format_product_list_item,
    format_stats,
)

router = Router()

_PAGE_SIZE = 10


# ================================================================== /start


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    text = (
        "Привет! Я слежу за ценами на Wildberries и уведомляю об изменениях.\n\n"
        "<b>Команды:</b>\n"
        "/add — добавить товар для мониторинга\n"
        "/list — список ваших товаров\n"
        "/delete — удалить товар\n"
        "/pause — приостановить мониторинг товара\n"
        "/resume — возобновить мониторинг\n"
        "/stats — ваша статистика\n"
        "/cancel — отменить текущее действие"
    )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="HTML")


# =================================================================== /add


@router.message(Command("add"))
@router.message(F.text == "➕ Добавить товар")
async def cmd_add(message: Message, state: FSMContext) -> None:
    await state.set_state(AddProductStates.waiting_for_url)
    await message.answer(
        "Пришлите ссылку на товар Wildberries.\n"
        "Например: <code>https://www.wildberries.ru/catalog/123456789/detail.aspx</code>\n\n"
        "/cancel — отменить",
        parse_mode="HTML",
    )


@router.message(AddProductStates.waiting_for_url)
async def process_url(message: Message, state: FSMContext, parser: WBParser) -> None:
    url = (message.text or "").strip()
    log = logger.bind(user_id=message.from_user.id)

    # Step 1: validate URL — stay in state on failure (no attempt counter)
    from parser.wb_parser import extract_article, _ARTICLE_RE
    if not _ARTICLE_RE.search(url):
        await message.answer(
            "Не похоже на ссылку Wildberries. Попробуйте ещё раз или /cancel"
        )
        return

    # Step 2: check per-user product limit
    count = await db.count_user_products(message.from_user.id)
    if count >= settings.MAX_PRODUCTS_PER_USER:
        await message.answer(
            f"Достигнут лимит: не более {settings.MAX_PRODUCTS_PER_USER} товаров.\n"
            "Удалите ненужные через /delete и попробуйте снова."
        )
        await state.clear()
        return

    # Step 3: parse product info from WB
    try:
        info = await parser.parse_url(url)
    except ProductNotFoundError:
        await message.answer("Товар не найден на Wildberries.")
        await state.clear()
        return
    except WBParserError:
        log.warning(f"WB parse error for url={url}")
        await message.answer(
            "Не удалось получить данные с Wildberries. Попробуйте позже."
        )
        await state.clear()
        return
    except Exception:
        log.exception(f"Unexpected error parsing url={url}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
        await state.clear()
        return

    # Step 4: save to database
    product_id = await db.add_product(message.from_user.id, info)
    if product_id is None:
        await message.answer("Этот товар уже отслеживается.")
        await state.clear()
        return

    # Step 5: success
    await message.answer(format_product_added(info))
    await state.clear()


# ================================================================== /list


@router.message(Command("list"))
@router.message(F.text == "📋 Мои товары")
async def cmd_list(message: Message) -> None:
    await _show_list(message, user_id=message.from_user.id, page=0)


@router.callback_query(F.data.startswith("list_page:"))
async def cb_list_page(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    await _show_list(
        callback.message,
        user_id=callback.from_user.id,
        page=page,
        edit=True,
    )
    await callback.answer()


async def _show_list(
    target: Message, user_id: int, page: int, edit: bool = False
) -> None:
    products = await db.get_user_products(user_id)

    if not products:
        text = "Список пуст. Добавьте товар через /add"
        if edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
        return

    start = page * _PAGE_SIZE
    page_items = products[start : start + _PAGE_SIZE]
    total_pages = (len(products) + _PAGE_SIZE - 1) // _PAGE_SIZE

    lines = [f"<b>📋 Мои товары</b> (стр. {page + 1}/{total_pages}):\n"]
    for i, product in enumerate(page_items, start=start + 1):
        lines.append(format_product_list_item(product, i))

    text = "\n\n".join(lines)
    kb = products_list_kb(products, page)

    if edit:
        await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


# ================================================================ /delete


@router.message(Command("delete"))
async def cmd_delete(message: Message, command: CommandObject) -> None:
    user_id = message.from_user.id

    if command.args:
        try:
            product_id = int(command.args)
        except ValueError:
            await message.answer("Укажите корректный ID: <code>/delete 42</code>", parse_mode="HTML")
            return
        await _ask_delete_confirm(message, user_id, product_id)
        return

    # No ID — show product selection list
    products = await db.get_user_products(user_id)
    if not products:
        await message.answer("Список пуст.")
        return

    builder = InlineKeyboardBuilder()
    for p in products[:20]:
        builder.button(
            text=f"🗑 {_truncate(p['name'], 30)}",
            callback_data=f"delete:{p['id']}",
        )
    builder.adjust(1)
    await message.answer(
        "Выберите товар для удаления:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("delete:"))
async def cb_delete_select(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[1])
    product = await db.get_product_by_id(product_id, callback.from_user.id)
    if not product:
        await callback.answer("Товар не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        f"Удалить товар «{_truncate(product['name'])}»?",
        reply_markup=delete_confirm_kb(product_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_confirm:"))
async def cb_delete_confirm(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[1])
    deleted = await db.delete_product(product_id, callback.from_user.id)
    if deleted:
        await callback.message.edit_text("✅ Товар удалён.")
    else:
        await callback.message.edit_text("Товар не найден.")
    await callback.answer()


@router.callback_query(F.data == "delete_cancel")
async def cb_delete_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Отменено.")
    await callback.answer()


async def _ask_delete_confirm(
    message: Message, user_id: int, product_id: int
) -> None:
    product = await db.get_product_by_id(product_id, user_id)
    if not product:
        await message.answer("Товар не найден или не принадлежит вам.")
        return
    await message.answer(
        f"Удалить товар «{_truncate(product['name'])}»?",
        reply_markup=delete_confirm_kb(product_id),
    )


# ================================================================= /pause


@router.message(Command("pause"))
async def cmd_pause(message: Message, command: CommandObject) -> None:
    user_id = message.from_user.id

    if command.args:
        try:
            product_id = int(command.args)
        except ValueError:
            await message.answer("Укажите корректный ID: <code>/pause 42</code>", parse_mode="HTML")
            return
        paused = await db.pause_product(product_id, user_id)
        if paused:
            product = await db.get_product_by_id(product_id, user_id)
            name = _truncate(product["name"]) if product else "товар"
            await message.answer(
                f"⏸ Мониторинг приостановлен: {name}",
                reply_markup=product_actions_kb(product_id, is_paused=True),
            )
        else:
            await message.answer("Товар не найден или не принадлежит вам.")
        return

    # No ID — show active products list
    products = await db.get_user_products(user_id)
    active = [p for p in products if not p["is_paused"]]
    if not active:
        await message.answer("Нет активных товаров для паузы.")
        return

    builder = InlineKeyboardBuilder()
    for p in active[:20]:
        builder.button(
            text=f"⏸ {_truncate(p['name'], 30)}",
            callback_data=f"pause:{p['id']}",
        )
    builder.adjust(1)
    await message.answer(
        "Выберите товар для паузы:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("pause:"))
async def cb_pause(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[1])
    paused = await db.pause_product(product_id, callback.from_user.id)
    if paused:
        await callback.message.edit_reply_markup(
            reply_markup=product_actions_kb(product_id, is_paused=True)
        )
        await callback.answer("⏸ Мониторинг приостановлен")
    else:
        await callback.answer("Товар не найден.", show_alert=True)


# ================================================================ /resume


@router.message(Command("resume"))
async def cmd_resume(message: Message, command: CommandObject, parser: WBParser) -> None:
    user_id = message.from_user.id

    if command.args:
        try:
            product_id = int(command.args)
        except ValueError:
            await message.answer("Укажите корректный ID: <code>/resume 42</code>", parse_mode="HTML")
            return
        await _do_resume(message, user_id, product_id, parser)
        return

    # No ID — show paused/unavailable products list
    products = await db.get_user_products(user_id)
    paused = [p for p in products if p["is_paused"] or not p.get("available", 1)]
    if not paused:
        await message.answer("Нет товаров на паузе.")
        return

    builder = InlineKeyboardBuilder()
    for p in paused[:20]:
        builder.button(
            text=f"▶️ {_truncate(p['name'], 30)}",
            callback_data=f"resume:{p['id']}",
        )
    builder.adjust(1)
    await message.answer(
        "Выберите товар для возобновления:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("resume:"))
async def cb_resume(callback: CallbackQuery, parser: WBParser) -> None:
    product_id = int(callback.data.split(":")[1])
    await callback.answer()
    await _do_resume(callback.message, callback.from_user.id, product_id, parser)


async def _do_resume(
    target: Message, user_id: int, product_id: int, parser: WBParser
) -> None:
    log = logger.bind(user_id=user_id)

    resumed = await db.resume_product(product_id, user_id)
    if not resumed:
        await target.answer("Товар не найден или не принадлежит вам.")
        return

    product = await db.get_product_by_id(product_id, user_id)
    await target.answer(f"▶️ Мониторинг возобновлён: {_truncate(product['name'])}")

    # Immediate price check after resume
    try:
        info = await parser.parse_article(product["wb_article"])
        if info.current_price != product["current_price"]:
            old_price = product["current_price"]
            await db.update_price(product_id, info.current_price)
            await target.answer(
                format_price_change(product, old_price, info.current_price),
                reply_markup=open_wb_kb(product["url"]),
            )
    except ProductNotFoundError:
        await db.set_unavailable(product_id)
        await target.answer(
            f"⚠️ Товар не найден на WB. Мониторинг снова приостановлен."
        )
    except WBParserError:
        log.warning(f"Could not check price immediately after resume for product {product_id}")
    except Exception:
        log.exception(f"Unexpected error on resume check for product {product_id}")


# ================================================================= /stats


@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def cmd_stats(message: Message) -> None:
    stats = await db.get_user_stats(message.from_user.id)
    await message.answer(format_stats(stats))


# ================================================================ /cancel


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current:
        await message.answer(
            "Действие отменено.", reply_markup=main_menu_kb()
        )
    else:
        await message.answer(
            "Нечего отменять.", reply_markup=main_menu_kb()
        )
