import asyncio
import re

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from config import settings
from parser.models import ProductInfo

_ARTICLE_RE = re.compile(r"/catalog/(\d+)/")

# JavaScript executed inside the browser to fetch a single product.
# Returns a dict with product data or {error: str, ...}.
_WB_FETCH_JS = """
async (article) => {
    const url = 'https://www.wildberries.ru/__internal/u-card/cards/v4/detail'
        + '?appType=1&curr=rub&dest=-1257786&spp=30&hide_vflags=4294967296'
        + '&ab_testing=false&lang=ru&nm=' + article;
    let resp;
    try {
        resp = await fetch(url, {
            headers: {
                'x-requested-with': 'XMLHttpRequest',
                'x-spa-version': '14.4.1',
                'deviceid': (localStorage.getItem('wbx__sessionID') || ''),
            }
        });
    } catch(e) {
        return { error: 'fetch_failed', message: e.message };
    }
    const text = await resp.text();
    if (text.trimStart().startsWith('<')) {
        return { error: 'got_html', status: resp.status };
    }
    let data;
    try { data = JSON.parse(text); } catch(e) {
        return { error: 'json_parse', message: e.message };
    }
    const products = (data && data.products) || [];
    if (!products.length) {
        return { error: 'not_found' };
    }
    const p = products[0];
    const sizes = (p && p.sizes) || [];
    const price = (sizes[0] && sizes[0].price) || {};
    return {
        name: p.name || null,
        salePriceU: price.product || null,
        priceU: price.basic || null,
        totalQuantity: (p.totalQuantity != null) ? p.totalQuantity : (p.quantity || 0),
    };
}
"""


class WBParserError(Exception):
    """General WB parser error (network failure, unexpected response, etc.)."""


class ProductNotFoundError(WBParserError):
    """Product not found on WB (API returned empty array or no price)."""


def extract_article(url: str) -> str:
    """Extract numeric article from any WB catalog URL.

    Supports:
      https://www.wildberries.ru/catalog/123456789/detail.aspx
      https://wildberries.ru/catalog/123456789/detail.aspx
      https://wb.ru/catalog/123456789/detail.aspx
    """
    match = _ARTICLE_RE.search(url)
    if not match:
        raise WBParserError(f"Cannot extract article from URL: {url}")
    return match.group(1)


class WBParser:
    """Async WB product parser backed by a headless Chromium browser.

    Uses playwright to bypass WB's bot-protection (PoW challenge).
    Use as an async context manager — the browser starts in __aenter__
    and is closed in __aexit__.
    """

    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None

    async def __aenter__(self) -> "WBParser":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        # Hide headless indicator
        await self._ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self._page = await self._ctx.new_page()

        # Warm up: visit WB main page so the browser solves the PoW challenge
        # and stores the x_wbaas_token cookie required for API calls.
        try:
            await self._page.goto(
                "https://www.wildberries.ru/",
                wait_until="networkidle",
                timeout=30_000,
            )
            # Wait up to 10 s for the antibot token to appear
            for _ in range(10):
                has_token = await self._page.evaluate(
                    "() => document.cookie.includes('x_wbaas_token')"
                )
                if has_token:
                    logger.info("WB session ready (x_wbaas_token obtained)")
                    break
                await asyncio.sleep(1)
            else:
                logger.warning("x_wbaas_token not obtained — API calls may fail")
        except Exception as exc:
            logger.warning(f"WB session warm-up failed: {exc}")

        return self

    async def __aexit__(self, *args: object) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    # ---------------------------------------------------------------- public

    async def parse_url(self, url: str) -> ProductInfo:
        """Parse product info from a WB catalog URL."""
        article = extract_article(url)
        info = await self.parse_article(article)
        info.url = url  # preserve the URL supplied by the user
        return info

    async def parse_article(self, article: str) -> ProductInfo:
        """Parse product info by WB article number."""
        log = logger.bind(article=article)
        log.debug("Fetching product")
        product = await self._fetch_with_retry(article)
        return ProductInfo(
            article=article,
            name=product["name"],
            current_price=product["salePriceU"],
            original_price=product["priceU"],
            discount_pct=_calc_discount(product["salePriceU"], product["priceU"]),
            url=f"https://www.wildberries.ru/catalog/{article}/detail.aspx",
            available=product.get("totalQuantity", 0) > 0,
        )

    async def parse_many(
        self, articles: list[str]
    ) -> list[ProductInfo | Exception]:
        """Parse multiple articles sequentially with rate-limit delay.

        Never raises — exceptions are captured per-article and included in
        the returned list at the same index as the input article.
        """
        results: list[ProductInfo | Exception] = []
        for i, article in enumerate(articles):
            if i > 0:
                await asyncio.sleep(0.5)
            try:
                results.append(await self.parse_article(article))
            except Exception as exc:
                results.append(exc)
        assert len(results) == len(articles)  # order must be preserved
        return results

    # --------------------------------------------------------------- private

    async def _fetch_product(self, article: str) -> dict:
        """Execute the in-browser fetch and return the raw product dict."""
        return await self._page.evaluate(_WB_FETCH_JS, article)

    async def _fetch_with_retry(self, article: str) -> dict:
        """Fetch product with retry logic for transient failures."""
        last_error: Exception = WBParserError(
            f"Max retries exceeded for article {article}"
        )

        for attempt in range(settings.WB_MAX_RETRIES):
            try:
                result = await self._fetch_product(article)
            except Exception as exc:
                last_error = WBParserError(
                    f"Browser error fetching article {article}: {exc}"
                )
                if attempt < settings.WB_MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                continue

            error = result.get("error")

            if error == "not_found":
                raise ProductNotFoundError(
                    f"Article {article} not found (empty products array)"
                )

            if error in ("got_html", "fetch_failed", "json_parse"):
                # Transient bot-challenge or network error — retry
                last_error = WBParserError(
                    f"Transient error '{error}' fetching article {article}"
                )
                if attempt < settings.WB_MAX_RETRIES - 1:
                    delay = 2 ** attempt
                    logger.warning(
                        f"Transient error for article {article} "
                        f"(attempt {attempt + 1}/{settings.WB_MAX_RETRIES}), "
                        f"retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)
                continue

            if error:
                raise WBParserError(
                    f"Unexpected error '{error}' for article {article}"
                )

            if result.get("salePriceU") is None:
                raise ProductNotFoundError(
                    f"Article {article} has no price (possibly removed)"
                )

            return result

        raise last_error


def _calc_discount(sale_price: int | None, original: int | None) -> int:
    if not sale_price or not original or original == 0:
        return 0
    return round((1 - sale_price / original) * 100)
