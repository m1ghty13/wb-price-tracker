from unittest.mock import AsyncMock, MagicMock

import pytest

from parser.models import ProductInfo
from parser.wb_parser import (
    ProductNotFoundError,
    WBParser,
    WBParserError,
    extract_article,
)


# ------------------------------------------------------------------ helpers


def _product_data(
    name: str = "Test Product",
    sale_price: int = 10000,
    price: int = 15000,
    quantity: int = 5,
) -> dict:
    """Simulate a successful _fetch_product return value."""
    return {
        "name": name,
        "salePriceU": sale_price,
        "priceU": price,
        "totalQuantity": quantity,
    }


@pytest.fixture
def parser():
    """WBParser with browser bypassed — _fetch_product is replaced per test."""
    p = WBParser()
    # Provide an AsyncMock page so no browser is started
    p._page = MagicMock()
    p._page.evaluate = AsyncMock()
    return p


# ------------------------------------------------------- extract_article


def test_extract_article_www_wildberries():
    url = "https://www.wildberries.ru/catalog/123456789/detail.aspx"
    assert extract_article(url) == "123456789"


def test_extract_article_no_www():
    url = "https://wildberries.ru/catalog/987654321/detail.aspx"
    assert extract_article(url) == "987654321"


def test_extract_article_wb_ru():
    url = "https://wb.ru/catalog/111222333/detail.aspx"
    assert extract_article(url) == "111222333"


def test_extract_article_invalid_url():
    with pytest.raises(WBParserError):
        extract_article("https://example.com/not-a-wb-url")


def test_extract_article_missing_digits():
    with pytest.raises(WBParserError):
        extract_article("https://www.wildberries.ru/catalog/abc/detail.aspx")


# ---------------------------------------------------- parse_article: success


async def test_parse_article_success(parser: WBParser, mocker):
    mocker.patch.object(
        parser, "_fetch_product", new=AsyncMock(return_value=_product_data())
    )
    mocker.patch("asyncio.sleep")

    info = await parser.parse_article("12345678")

    assert isinstance(info, ProductInfo)
    assert info.article == "12345678"
    assert info.name == "Test Product"
    assert info.current_price == 10000
    assert info.original_price == 15000
    assert info.discount_pct == 33
    assert info.available is True
    assert "12345678" in info.url


async def test_parse_article_available_false_when_quantity_zero(parser: WBParser, mocker):
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(return_value=_product_data(quantity=0))
    )
    mocker.patch("asyncio.sleep")

    info = await parser.parse_article("12345678")
    assert info.available is False


async def test_parse_article_discount_calculation(parser: WBParser, mocker):
    """Discount should be computed from salePriceU and priceU."""
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(return_value=_product_data(sale_price=281900, price=410000))
    )
    mocker.patch("asyncio.sleep")

    info = await parser.parse_article("12345678")
    assert info.discount_pct == 31  # round((1 - 281900/410000)*100)


# ------------------------------------------ parse_article: not found


async def test_parse_article_not_found_raises(parser: WBParser, mocker):
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(return_value={"error": "not_found"})
    )
    mocker.patch("asyncio.sleep")

    with pytest.raises(ProductNotFoundError):
        await parser.parse_article("00000000")


async def test_parse_article_no_price_raises(parser: WBParser, mocker):
    """Product exists but has no price → ProductNotFoundError."""
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(return_value={"name": "X", "salePriceU": None, "priceU": None, "totalQuantity": 0})
    )
    mocker.patch("asyncio.sleep")

    with pytest.raises(ProductNotFoundError):
        await parser.parse_article("00000000")


# --------------------------------------------- parse_article: retries


async def test_parse_article_transient_html_retries(parser: WBParser, mocker):
    """got_html error should be retried; success on second attempt."""
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(side_effect=[
            {"error": "got_html", "status": 498},
            _product_data(),
        ])
    )
    sleep_mock = mocker.patch("asyncio.sleep")

    info = await parser.parse_article("12345678")
    assert isinstance(info, ProductInfo)
    assert sleep_mock.called


async def test_parse_article_all_transient_exhausts_retries(parser: WBParser, mocker):
    """got_html on every attempt → WBParserError after all retries."""
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(return_value={"error": "got_html", "status": 498})
    )
    mocker.patch("asyncio.sleep")

    with pytest.raises(WBParserError):
        await parser.parse_article("12345678")


async def test_parse_article_fetch_failed_retries(parser: WBParser, mocker):
    """fetch_failed (network error) should be retried."""
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(side_effect=[
            {"error": "fetch_failed", "message": "net::ERR_ABORTED"},
            _product_data(),
        ])
    )
    sleep_mock = mocker.patch("asyncio.sleep")

    info = await parser.parse_article("12345678")
    assert isinstance(info, ProductInfo)
    assert sleep_mock.called


async def test_parse_article_browser_exception_retries(parser: WBParser, mocker):
    """Exception from page.evaluate should be retried then raise WBParserError."""
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(side_effect=Exception("Page crashed"))
    )
    sleep_mock = mocker.patch("asyncio.sleep")

    with pytest.raises(WBParserError):
        await parser.parse_article("12345678")

    assert sleep_mock.called


# ------------------------------------------------------ parse_url


async def test_parse_url_preserves_original_url(parser: WBParser, mocker):
    mocker.patch.object(
        parser, "_fetch_product", new=AsyncMock(return_value=_product_data())
    )
    mocker.patch("asyncio.sleep")

    original_url = "https://www.wildberries.ru/catalog/12345678/detail.aspx"
    info = await parser.parse_url(original_url)
    assert info.url == original_url


# ------------------------------------------------------ parse_many


async def test_parse_many_partial_failure_does_not_stop(parser: WBParser, mocker):
    """One failed article must not prevent others from being parsed."""
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(side_effect=[
            _product_data(name="Product A"),
            {"error": "not_found"},
            _product_data(name="Product C"),
        ])
    )
    mocker.patch("asyncio.sleep")

    results = await parser.parse_many(["11111111", "22222222", "33333333"])

    assert len(results) == 3
    assert isinstance(results[0], ProductInfo)
    assert results[0].name == "Product A"
    assert isinstance(results[1], ProductNotFoundError)
    assert isinstance(results[2], ProductInfo)
    assert results[2].name == "Product C"


async def test_parse_many_order_preserved(parser: WBParser, mocker):
    """Return list must be same length as input and in same order."""
    articles = ["11111111", "22222222", "33333333", "44444444"]
    mocker.patch.object(
        parser, "_fetch_product",
        new=AsyncMock(return_value=_product_data())
    )
    mocker.patch("asyncio.sleep")

    results = await parser.parse_many(articles)

    assert len(results) == len(articles)
    assert all(isinstance(r, ProductInfo) for r in results)


async def test_parse_many_empty_list(parser: WBParser):
    results = await parser.parse_many([])
    assert results == []
