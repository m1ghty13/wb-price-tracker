from parser.models import ProductInfo


def _rub(kopecks: int) -> str:
    """Convert kopecks to a formatted ruble string: 8999000 → '89 990 ₽'."""
    rubles = kopecks // 100
    return f"{rubles:,}".replace(",", "\u00a0") + "\u00a0₽"


def _pct(old: int, new: int) -> int:
    """Absolute percent change between two prices (rounded)."""
    if old == 0:
        return 0
    return round(abs(new - old) / old * 100)


def _truncate(name: str, max_len: int = 40) -> str:
    """Truncate name to max_len chars, appending ellipsis if cut."""
    if len(name) <= max_len:
        return name
    return name[: max_len - 1] + "…"


# ------------------------------------------------------------------ public


def format_product_added(info: ProductInfo) -> str:
    lines = [
        "✅ Товар добавлен!",
        f"📦 {info.name}",
        f"💰 Цена: {_rub(info.current_price)} (было {_rub(info.original_price)})",
        f"🏷 Скидка: {info.discount_pct}%",
        "🔔 Буду уведомлять при изменении цены",
    ]
    return "\n".join(lines)


def format_product_list_item(product: dict, index: int) -> str:
    name = _truncate(product["name"])
    price = _rub(product["current_price"])
    discount = product["discount_pct"]
    paused_mark = " ⏸" if product.get("is_paused") else ""
    unavail_mark = " ❌" if not product.get("available", 1) else ""
    last_checked = product.get("last_checked", "—")[:16]  # "YYYY-MM-DD HH:MM"

    return (
        f"{index}. {name}{paused_mark}{unavail_mark}\n"
        f"   💰 {price} (-{discount}%)\n"
        f"   🕐 {last_checked}"
    )


def format_price_change(product: dict, old_price: int, new_price: int) -> str:
    if new_price < old_price:
        header = "📉 Цена снизилась!"
        arrow = "▼"
    else:
        header = "📈 Цена выросла!"
        arrow = "▲"

    pct = _pct(old_price, new_price)
    name = _truncate(product["name"])

    return (
        f"{header}\n"
        f"📦 {name}\n"
        f"Было: {_rub(old_price)} → Стало: {_rub(new_price)} ({arrow} {pct}%)"
    )


def format_stats(stats: dict) -> str:
    total = stats.get("total", 0)
    paused = stats.get("paused", 0)
    biggest_drop = stats.get("biggest_drop", 0)
    most_active_changes = stats.get("most_active_changes", 0)
    total_savings = stats.get("total_savings", 0)

    lines = [
        "📊 Ваша статистика",
        "",
        f"🛍 Всего товаров: {total} (на паузе: {paused})",
    ]

    if biggest_drop < 0:
        lines.append(f"📉 Лучшая цена: сэкономил {_rub(abs(biggest_drop))}")
    else:
        lines.append("📉 Лучшая цена: данных пока нет")

    if most_active_changes > 0:
        lines.append(f"🔄 Активнее всего: {most_active_changes} изменений цены")
    else:
        lines.append("🔄 Активнее всего: изменений пока нет")

    if total_savings > 0:
        lines.append(f"💰 Общая экономия: {_rub(total_savings)}")
    else:
        lines.append("💰 Общая экономия: 0\u00a0₽")

    return "\n".join(lines)
