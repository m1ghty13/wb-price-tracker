# WB Price Tracker

Telegram-бот мониторинга цен на Wildberries. Добавляйте товары по ссылке — бот уведомит при изменении цены.

## Стек

- Python 3.11+, aiogram 3.x, aiosqlite, APScheduler, httpx
- pydantic-settings, loguru, pytest

## Быстрый старт

```bash
git clone <repo>
cd wb_price_tracker
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Отредактируйте .env: укажите BOT_TOKEN и ADMIN_IDS
python main.py
```

## Конфигурация (.env)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — | Токен от @BotFather (обязательно) |
| `ADMIN_IDS` | — | ID администраторов через запятую |
| `ALLOWED_USERS` | пусто | Список разрешённых user_id (пусто = открытый бот) |
| `CHECK_INTERVAL_HOURS` | 2 | Интервал проверки цен (часы) |
| `MAX_PRODUCTS_PER_USER` | 50 | Лимит товаров на пользователя |
| `LOG_LEVEL` | INFO | Уровень логирования |
| `DATABASE_PATH` | data/tracker.db | Путь к БД |
| `WB_REQUEST_TIMEOUT` | 10 | Таймаут запросов к WB (сек) |
| `WB_MAX_RETRIES` | 3 | Попыток при ошибке запроса |

## Команды бота

| Команда | Описание |
|---|---|
| `/start` | Приветствие и список команд |
| `/add` | Добавить товар по ссылке WB |
| `/list` | Список отслеживаемых товаров |
| `/delete [id]` | Удалить товар |
| `/pause [id]` | Приостановить мониторинг |
| `/resume [id]` | Возобновить мониторинг + проверить цену |
| `/stats` | Личная статистика |
| `/cancel` | Отменить текущее действие |

**Только для администраторов:**

| Команда | Описание |
|---|---|
| `/admin_stats` | Статистика бота |
| `/broadcast` | Рассылка всем пользователям |

## Деплой на сервере

```bash
# Первичная установка
sudo mkdir -p /opt/wb_price_tracker
sudo cp -r . /opt/wb_price_tracker/
cd /opt/wb_price_tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env  # заполните токен и ADMIN_IDS

# Установка systemd-сервиса
sudo cp deploy/wb-tracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable wb-tracker
sudo systemctl start wb-tracker
sudo systemctl status wb-tracker
```

**Обновление (после git push):**

```bash
cd /opt/wb_price_tracker && bash deploy/setup.sh
```

## Тесты

```bash
pytest -v
```

## Структура проекта

```
wb_price_tracker/
├── bot/
│   ├── handlers/
│   │   ├── user.py     # /start /add /list /delete /pause /resume /stats
│   │   └── admin.py    # /admin_stats /broadcast
│   ├── keyboards.py
│   ├── middlewares.py  # AuthMiddleware
│   ├── filters.py      # IsAdmin
│   └── states.py       # FSM states
├── parser/
│   ├── wb_parser.py    # WBParser + retry logic
│   └── models.py       # ProductInfo dataclass
├── db/
│   └── database.py     # Database class + CRUD
├── scheduler/
│   └── jobs.py         # check_prices, cleanup_old_history
├── utils/
│   └── formatters.py   # Message formatters
├── tests/
├── deploy/
├── config.py
├── main.py
└── requirements.txt
```

## Логи

Файл: `logs/tracker.log` — ротация 10 МБ, хранение 7 дней.
