# WB Price Tracker Bot — Design Spec
**Date:** 2026-04-06  
**Status:** Approved

---

## Overview

Production-ready Telegram-бот мониторинга цен на Wildberries. Пользователь добавляет ссылки на товары, бот периодически проверяет цены и уведомляет об изменениях.

---

## Stack

| Layer | Technology |
|---|---|
| Bot framework | aiogram 3.x (async handlers, FSM, middleware) |
| Database | aiosqlite (raw SQL, no ORM) |
| Scheduler | APScheduler 3.x (AsyncIOScheduler) |
| HTTP client | httpx (async) |
| Config | pydantic-settings + python-dotenv |
| Logging | loguru |
| Tests | pytest + pytest-asyncio |

---

## Project Structure

```
wb_price_tracker/
├── bot/
│   ├── handlers/
│   │   ├── user.py         # /start, /add, /list, /delete, /pause, /resume, /stats
│   │   └── admin.py        # /admin_stats, /broadcast
│   ├── keyboards.py
│   ├── middlewares.py      # AuthMiddleware
│   ├── filters.py          # IsAdmin
│   └── states.py           # FSM states
├── parser/
│   ├── wb_parser.py        # WBParser async context manager
│   └── models.py           # ProductInfo dataclass
├── db/
│   └── database.py         # Database class + all CRUD
├── scheduler/
│   └── jobs.py             # check_prices, cleanup_old_history
├── utils/
│   └── formatters.py
├── tests/
│   ├── test_parser.py
│   ├── test_db.py
│   └── test_formatters.py
├── deploy/
│   ├── wb-tracker.service
│   └── setup.sh
├── config.py
├── main.py
├── requirements.txt
└── .env.example
```

---

## Section 1: Database Schema & CRUD

### Schema

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    wb_article      TEXT NOT NULL,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    current_price   INTEGER NOT NULL,       -- копейки
    original_price  INTEGER NOT NULL,       -- копейки
    discount_pct    INTEGER DEFAULT 0,
    is_paused       INTEGER DEFAULT 0,
    available       INTEGER DEFAULT 1,      -- 0 = товар недоступен на WB
    added_at        TEXT DEFAULT (datetime('now')),
    last_checked    TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, wb_article)
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL,
    price       INTEGER NOT NULL,           -- копейки
    recorded_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id);
CREATE INDEX IF NOT EXISTS idx_history_product_id ON price_history(product_id);
CREATE INDEX IF NOT EXISTS idx_history_recorded_at ON price_history(recorded_at);
```

**Цены хранятся в копейках (integer). Конвертация в рубли только при форматировании.**

### Database class

Один глобальный объект `Database` с единым aiosqlite соединением. Инициализируется в `on_startup`, закрывается в `on_shutdown`. Не создавать дополнительных соединений в scheduler или других модулях.

### CRUD functions (все async)

| Функция | Описание |
|---|---|
| `upsert_user(user_id, username)` | Создать или обновить пользователя |
| `add_product(user_id, product_info) → int \| None` | None если дубль (UNIQUE constraint) |
| `get_user_products(user_id) → list[dict]` | Все товары пользователя |
| `get_product_by_id(product_id, user_id) → dict \| None` | Проверяет владельца |
| `delete_product(product_id, user_id) → bool` | Cascade удаляет history |
| `pause_product(product_id, user_id) → bool` | is_paused = 1 |
| `resume_product(product_id, user_id) → bool` | is_paused = 0, available = 1 |
| `update_price(product_id, new_price)` | Обновляет current_price, last_checked + пишет в history |
| `get_price_history(product_id, limit=10) → list[dict]` | |
| `get_all_active_products() → list[dict]` | WHERE is_paused=0 AND available=1 |
| `count_user_products(user_id) → int` | |
| `get_db_stats() → dict` | Для /admin_stats |
| `get_user_stats(user_id) → dict` | Для /stats |
| `set_unavailable(product_id)` | available = 0 (товар исчез с WB) |
| `set_user_inactive(user_id)` | is_active = 0 (бот заблокирован) |
| `cleanup_history() → int` | Удаляет записи > 90 дней, возвращает rowcount |

### get_db_stats — уведомления за 24ч

Считать через `price_history`:
```sql
SELECT COUNT(*) FROM price_history WHERE recorded_at >= datetime('now', '-1 day')
```
Каждая запись в history = одно уведомление об изменении цены. Отдельной таблицы нет.

### get_user_stats — формулы

```sql
-- biggest_drop: максимальное разовое падение по одному товару
SELECT MIN(h2.price - h1.price) as drop
FROM price_history h1
JOIN price_history h2 ON h1.product_id = h2.product_id
WHERE h2.recorded_at > h1.recorded_at
AND h1.product_id IN (SELECT id FROM products WHERE user_id = ?)

-- most_active: товар с наибольшим количеством изменений цены
SELECT product_id, COUNT(*) as changes
FROM price_history
WHERE product_id IN (SELECT id FROM products WHERE user_id = ?)
GROUP BY product_id
ORDER BY changes DESC
LIMIT 1
```

`total_savings` считается в Python из истории (сумма всех отрицательных дельт), не в SQL — SQL запрос был бы нечитаемым.

---

## Section 2: Parser

### ProductInfo dataclass

```python
@dataclass
class ProductInfo:
    article: str
    name: str
    current_price: int    # копейки
    original_price: int   # копейки
    discount_pct: int
    url: str
    available: bool
```

### WBParser — async context manager

```python
class WBParser:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
            timeout=settings.WB_REQUEST_TIMEOUT
        )
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()
```

Инициализируется в `main()` через `async with WBParser() as parser:`, инстанс передаётся в scheduler через `kwargs`. `aclose()` гарантирован при любом завершении.

### URL → Article

```python
re.search(r'/catalog/(\d+)/', url)
```
Покрывает форматы:
- `https://www.wildberries.ru/catalog/123456789/detail.aspx`
- `https://wildberries.ru/catalog/123456789/detail.aspx`
- `https://wb.ru/catalog/123456789/detail.aspx`

### WB API endpoint

```
https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&nm={article}
```

Парсинг: `data["data"]["products"][0]`
- `name`: `product["name"]`
- `current_price`: `product["salePriceU"]` (уже копейки)
- `original_price`: `product["priceU"]` (уже копейки)
- `discount_pct`: `product.get("sale", 0)`
- `available`: `product.get("quantity", 0) > 0`

### Retry logic

| Ситуация | Поведение |
|---|---|
| `httpx.TimeoutException` | Retry, backoff 1s → 2s → 4s |
| HTTP 429 | Retry с задержкой 10s |
| HTTP 5xx | Retry, backoff 1s → 2s → 4s |
| HTTP 404 | `ProductNotFoundError` (без retry) |
| Пустой `products[]` | `ProductNotFoundError` (без retry) |
| Исчерпаны все retry | `WBParserError` |
| Неизвестный HTTP статус | `WBParserError` (без retry) |

Максимум `WB_MAX_RETRIES` попыток.

### parse_many

Sequential loop с `asyncio.sleep(0.5)` между итерациями (не `asyncio.gather`) — иначе rate limit не соблюдается. Порядок результатов сохранён.

```python
async def parse_many(articles: list[str]) -> list[ProductInfo | Exception]:
    results = []
    for i, article in enumerate(articles):
        if i > 0:
            await asyncio.sleep(0.5)
        try:
            results.append(await parse_article(article))
        except Exception as e:
            results.append(e)
    return results
# assert len(results) == len(articles)  — критично для scheduler матчинга по индексу
```

---

## Section 3: Handlers, Keyboards, Middleware

### AuthMiddleware

```python
# Порядок важен: проверка ДО upsert
if allowed_users and user_id not in allowed_users:
    await message.answer("⛔ У вас нет доступа к этому боту.")
    return
await upsert_user(user_id, username)   # только прошедшие проверку
await handler(event, data)
```

### IsAdmin filter

```python
class IsAdmin(MagicFilter):
    def resolve(self, event: Message | CallbackQuery) -> bool:
        return event.from_user.id in settings.ADMIN_IDS
```
Non-admin → хендлер не срабатывает (silent ignore).

### FSM States

```python
class AddProductStates(StatesGroup):
    waiting_for_url = State()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()
```

### /add flow

```
/add → set AddProductStates.waiting_for_url → "Пришлите ссылку на товар WB"

Текст в состоянии:
  1. regex: r'/catalog/(\d+)/' — если не совпало → "Не похоже на ссылку WB" 
     (остаёмся в состоянии, без счётчика попыток, только /cancel сбрасывает)
  2. count_user_products >= MAX_PRODUCTS_PER_USER → ошибка + clear state
  3. parse_url() → WBParserError → понятная ошибка + clear state
  4. add_product() → None (дубль) → "Этот товар уже отслеживается" + clear state
  5. Успех → format_product_added() + clear state
```

### Keyboards

| Функция | Тип | Описание |
|---|---|---|
| `main_menu_kb()` | ReplyKeyboard | Основные команды |
| `products_list_kb(products, page)` | InlineKeyboard | Пагинация (10/стр), `list_page:{page}` |
| `delete_confirm_kb(product_id)` | InlineKeyboard | `delete_confirm:{id}` / `delete_cancel` |
| `product_actions_kb(product_id, is_paused)` | InlineKeyboard | Пауза/Возобновить + Удалить |
| `open_wb_kb(url)` | InlineKeyboard | Кнопка "Открыть на WB" (URL) |

### /resume

`resume_product()` (is_paused=0, available=1) + немедленный `parse_article()` + если цена изменилась → `update_price()` + сообщение пользователю.

### TelegramForbiddenError

В broadcaster и scheduler: `is_active=0` у пользователя через `set_user_inactive()`.

---

## Section 4: Scheduler + main.py

### check_prices job

```python
async def check_prices(bot: Bot, parser: WBParser):
    products = await db.get_all_active_products()  # WHERE is_paused=0 AND available=1
    for i, product in enumerate(products):
        if i > 0:
            await asyncio.sleep(0.5)
        try:
            info = await parser.parse_article(product["wb_article"])
            if info.current_price != product["current_price"]:
                old_price = product["current_price"]
                await db.update_price(product["id"], info.current_price)
                text = format_price_change(product, old_price, info.current_price)
                kb = open_wb_kb(product["url"])
                await bot.send_message(product["user_id"], text, reply_markup=kb)
        except ProductNotFoundError:
            await db.set_unavailable(product["id"])
            await bot.send_message(
                product["user_id"],
                f"⚠️ Товар {product['name']} недоступен на WB. Мониторинг приостановлен."
            )
        except TelegramForbiddenError:
            await db.set_user_inactive(product["user_id"])
        except Exception as e:
            logger.bind(job="check_prices").error(f"product {product['id']}: {e}")
```

### cleanup_old_history job

Использует тот же глобальный `db` объект — не создаёт новое соединение.

```python
async def cleanup_old_history():
    rowcount = await db.cleanup_history()
    logger.bind(job="cleanup_history").info(f"Deleted {rowcount} old records")
```

### main.py lifecycle

```python
async def main():
    logger.add("logs/tracker.log", rotation="10 MB", retention="7 days",
               format="{time} | {level} | {name} | {message}",
               level=settings.LOG_LEVEL)

    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)

    async with WBParser() as parser:
        bot = Bot(token=settings.BOT_TOKEN)
        dp = Dispatcher()
        # middleware, routers registration

        scheduler = AsyncIOScheduler()
        scheduler.add_job(check_prices, "interval",
                          hours=settings.CHECK_INTERVAL_HOURS,
                          id="check_prices",
                          kwargs={"bot": bot, "parser": parser})
        scheduler.add_job(cleanup_old_history, "cron",
                          hour=4, minute=0,
                          id="cleanup_history")

        async def on_startup():
            await db.init()
            scheduler.start()
            logger.info("Bot started")

        async def on_shutdown():
            scheduler.shutdown()
            await db.close()
            logger.info("Bot stopped")

        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)
        await dp.start_polling(bot)
```

### Error handler

```python
async def on_error(event: ErrorEvent, bot: Bot):
    logger.error(f"Unhandled: {event.exception}")
    if settings.ADMIN_IDS:
        await bot.send_message(
            settings.ADMIN_IDS[0],
            f"❌ Unhandled error:\n<code>{event.exception}</code>",
            parse_mode="HTML"
        )

dp.errors.register(on_error)
```

---

## Development Order

1. `config.py` + `.env.example` → проверить `python -c "from config import settings; print(settings)"`
2. `db/database.py` → `tests/test_db.py` — все тесты зелёные
3. `parser/models.py` + `parser/wb_parser.py` → `tests/test_parser.py`
4. `utils/formatters.py` → `tests/test_formatters.py`
5. `bot/states.py` + `bot/filters.py` + `bot/middlewares.py` + `bot/keyboards.py`
6. `bot/handlers/user.py` → проверить импорт
7. `bot/handlers/admin.py`
8. `scheduler/jobs.py`
9. `main.py` → `python main.py` без ошибок
10. `deploy/wb-tracker.service` + `deploy/setup.sh`

**Правило:** не переходить к следующему шагу без явного "ОК, следующий шаг".

---

## Key Constraints

- Цены только в копейках в БД, конвертация в рубли только в форматтерах
- `WBParser` — единственный async context manager, `aclose()` гарантирован через `async with` в `main()`
- `Database` — один глобальный объект, одно соединение, не создавать дополнительных
- `upsert_user` вызывается в middleware ПОСЛЕ проверки ALLOWED_USERS
- `/add` — без счётчика попыток, только `/cancel` сбрасывает состояние
- `parse_many` — sequential loop, не gather; порядок результатов сохранён
- `TelegramForbiddenError` → `set_user_inactive()` в любом месте отправки
