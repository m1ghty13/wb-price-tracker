import pytest

from parser.models import ProductInfo
from utils.formatters import (
    _rub,
    _truncate,
    format_price_change,
    format_product_added,
    format_product_list_item,
    format_stats,
)


# ------------------------------------------------------------------ _rub


def test_rub_exact_rubles():
    assert _rub(10000) == "100\u00a0₽"


def test_rub_thousands_separator():
    # 8 999 000 kopecks = 89 990 rubles
    result = _rub(8_999_000)
    assert "89" in result
    assert "990" in result
    assert "₽" in result


def test_rub_large_value():
    # 11 999 000 kopecks = 119 990 rubles
    result = _rub(11_999_000)
    assert "119" in result
    assert "990" in result


def test_rub_zero():
    assert _rub(0) == "0\u00a0₽"


# -------------------------------------------------------------- _truncate


def test_truncate_short_name_unchanged():
    name = "Samsung Galaxy"
    assert _truncate(name) == name


def test_truncate_exactly_40_chars_unchanged():
    name = "A" * 40
    assert _truncate(name) == name
    assert len(_truncate(name)) == 40


def test_truncate_41_chars_cuts_to_40():
    name = "A" * 41
    result = _truncate(name)
    assert len(result) == 40
    assert result.endswith("…")


def test_truncate_long_name():
    name = "Очень длинное название товара которое явно превышает сорок символов"
    result = _truncate(name)
    assert len(result) == 40
    assert result.endswith("…")


# ------------------------------------------------- format_price_change


def test_format_price_change_drop_shows_down_arrow():
    product = {"name": "Test Product", "url": "https://wb.ru/catalog/1/detail.aspx"}
    result = format_price_change(product, old_price=10000, new_price=8000)
    assert "▼" in result
    assert "▲" not in result
    assert "📉" in result


def test_format_price_change_rise_shows_up_arrow():
    product = {"name": "Test Product", "url": "https://wb.ru/catalog/1/detail.aspx"}
    result = format_price_change(product, old_price=8000, new_price=10000)
    assert "▲" in result
    assert "▼" not in result
    assert "📈" in result


def test_format_price_change_correct_percent_drop():
    # 10000 → 8000: drop of 20%
    product = {"name": "Test", "url": "https://wb.ru/catalog/1/detail.aspx"}
    result = format_price_change(product, old_price=10000, new_price=8000)
    assert "20%" in result


def test_format_price_change_correct_percent_rise():
    # 8000 → 10000: rise of 25%
    product = {"name": "Test", "url": "https://wb.ru/catalog/1/detail.aspx"}
    result = format_price_change(product, old_price=8000, new_price=10000)
    assert "25%" in result


def test_format_price_change_kopecks_to_rubles():
    # 1_000_000 kopecks = 10 000 rubles
    product = {"name": "Test", "url": "https://wb.ru/catalog/1/detail.aspx"}
    result = format_price_change(product, old_price=1_000_000, new_price=800_000)
    assert "10" in result   # 10 000 ₽
    assert "8" in result    # 8 000 ₽
    assert "₽" in result


def test_format_price_change_includes_product_name():
    product = {"name": "Samsung Galaxy S24", "url": "https://wb.ru/catalog/1/detail.aspx"}
    result = format_price_change(product, old_price=10000, new_price=8000)
    assert "Samsung Galaxy S24" in result


def test_format_price_change_truncates_long_name():
    product = {
        "name": "Очень длинное название товара которое явно превышает сорок символов",
        "url": "https://wb.ru/catalog/1/detail.aspx",
    }
    result = format_price_change(product, old_price=10000, new_price=8000)
    # Name in result must be at most 40 chars
    lines = result.splitlines()
    name_line = next(l for l in lines if "📦" in l)
    displayed_name = name_line.replace("📦 ", "")
    assert len(displayed_name) <= 40


# ----------------------------------------------- format_product_added


def test_format_product_added_contains_name():
    info = ProductInfo(
        article="12345678",
        name="Samsung Galaxy S24",
        current_price=8_999_000,
        original_price=11_999_000,
        discount_pct=25,
        url="https://wb.ru/catalog/12345678/detail.aspx",
        available=True,
    )
    result = format_product_added(info)
    assert "Samsung Galaxy S24" in result


def test_format_product_added_kopecks_to_rubles():
    info = ProductInfo(
        article="12345678",
        name="Test",
        current_price=8_999_000,   # 89 990 rubles
        original_price=11_999_000,  # 119 990 rubles
        discount_pct=25,
        url="https://wb.ru/catalog/12345678/detail.aspx",
        available=True,
    )
    result = format_product_added(info)
    assert "89" in result
    assert "119" in result
    assert "₽" in result


def test_format_product_added_shows_discount():
    info = ProductInfo(
        article="1",
        name="Test",
        current_price=10000,
        original_price=15000,
        discount_pct=33,
        url="https://wb.ru/1",
        available=True,
    )
    result = format_product_added(info)
    assert "33%" in result


# ----------------------------------------------- format_product_list_item


def test_format_product_list_item_truncates_name():
    product = {
        "name": "Очень длинное название товара которое явно превышает сорок символов",
        "current_price": 10000,
        "original_price": 15000,
        "discount_pct": 33,
        "is_paused": 0,
        "available": 1,
        "last_checked": "2026-04-06 22:00:00",
    }
    result = format_product_list_item(product, index=1)
    first_line = result.splitlines()[0]
    # Extract name (remove index, spaces, status marks)
    displayed_name = first_line.lstrip("0123456789. ")
    assert len(displayed_name) <= 40


def test_format_product_list_item_shows_paused_mark():
    product = {
        "name": "Test",
        "current_price": 10000,
        "original_price": 15000,
        "discount_pct": 10,
        "is_paused": 1,
        "available": 1,
        "last_checked": "2026-04-06 22:00:00",
    }
    result = format_product_list_item(product, index=1)
    assert "⏸" in result


def test_format_product_list_item_no_paused_mark_when_active():
    product = {
        "name": "Test",
        "current_price": 10000,
        "original_price": 15000,
        "discount_pct": 10,
        "is_paused": 0,
        "available": 1,
        "last_checked": "2026-04-06 22:00:00",
    }
    result = format_product_list_item(product, index=1)
    assert "⏸" not in result


def test_format_product_list_item_kopecks_to_rubles():
    product = {
        "name": "Test",
        "current_price": 1_000_000,  # 10 000 rubles
        "original_price": 1_500_000,
        "discount_pct": 33,
        "is_paused": 0,
        "available": 1,
        "last_checked": "2026-04-06 22:00:00",
    }
    result = format_product_list_item(product, index=1)
    assert "10" in result
    assert "₽" in result


def test_format_product_list_item_index():
    product = {
        "name": "Test",
        "current_price": 10000,
        "original_price": 15000,
        "discount_pct": 10,
        "is_paused": 0,
        "available": 1,
        "last_checked": "2026-04-06 22:00:00",
    }
    result = format_product_list_item(product, index=7)
    assert result.startswith("7.")


# ----------------------------------------------------------------- format_stats


def test_format_stats_shows_totals():
    stats = {
        "total": 5,
        "paused": 2,
        "biggest_drop": -50000,  # -500 rubles
        "most_active_product_id": 3,
        "most_active_changes": 10,
        "total_savings": 200000,  # 2000 rubles
    }
    result = format_stats(stats)
    assert "5" in result
    assert "2" in result


def test_format_stats_biggest_drop_kopecks_to_rubles():
    stats = {
        "total": 1,
        "paused": 0,
        "biggest_drop": -100000,  # -1000 rubles
        "most_active_product_id": 1,
        "most_active_changes": 3,
        "total_savings": 100000,
    }
    result = format_stats(stats)
    # 100000 kopecks = 1000 rubles
    assert "1" in result
    assert "₽" in result


def test_format_stats_no_history():
    stats = {
        "total": 3,
        "paused": 0,
        "biggest_drop": 0,
        "most_active_product_id": None,
        "most_active_changes": 0,
        "total_savings": 0,
    }
    result = format_stats(stats)
    # Should not crash, show "no data yet" messages
    assert "₽" in result or "нет" in result
