import aiosqlite
from pathlib import Path

from parser.models import ProductInfo

_SCHEMA = """
PRAGMA foreign_keys = ON;

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
    current_price   INTEGER NOT NULL,
    original_price  INTEGER NOT NULL,
    discount_pct    INTEGER DEFAULT 0,
    is_paused       INTEGER DEFAULT 0,
    available       INTEGER DEFAULT 1,
    added_at        TEXT DEFAULT (datetime('now')),
    last_checked    TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, wb_article)
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL,
    price       INTEGER NOT NULL,
    recorded_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id);
CREATE INDEX IF NOT EXISTS idx_history_product_id ON price_history(product_id);
CREATE INDEX IF NOT EXISTS idx_history_recorded_at ON price_history(recorded_at);
"""


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        # Enable foreign key enforcement (required for CASCADE DELETE)
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ users

    async def upsert_user(self, user_id: int, username: str | None) -> None:
        await self._conn.execute(
            """INSERT INTO users (user_id, username)
               VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET username = excluded.username""",
            (user_id, username),
        )
        await self._conn.commit()

    # --------------------------------------------------------------- products

    async def add_product(self, user_id: int, info: ProductInfo) -> int | None:
        """Insert product. Returns new row id, or None if duplicate article."""
        try:
            async with self._conn.execute(
                """INSERT INTO products
                       (user_id, wb_article, name, url,
                        current_price, original_price, discount_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    info.article,
                    info.name,
                    info.url,
                    info.current_price,
                    info.original_price,
                    info.discount_pct,
                ),
            ) as cur:
                await self._conn.commit()
                return cur.lastrowid
        except aiosqlite.IntegrityError:
            return None

    async def get_user_products(self, user_id: int) -> list[dict]:
        async with self._conn.execute(
            "SELECT * FROM products WHERE user_id = ? ORDER BY added_at DESC",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_product_by_id(
        self, product_id: int, user_id: int
    ) -> dict | None:
        async with self._conn.execute(
            "SELECT * FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_product(self, product_id: int, user_id: int) -> bool:
        async with self._conn.execute(
            "DELETE FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        ) as cur:
            await self._conn.commit()
            return cur.rowcount > 0

    async def pause_product(self, product_id: int, user_id: int) -> bool:
        async with self._conn.execute(
            "UPDATE products SET is_paused = 1 WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        ) as cur:
            await self._conn.commit()
            return cur.rowcount > 0

    async def resume_product(self, product_id: int, user_id: int) -> bool:
        async with self._conn.execute(
            """UPDATE products
               SET is_paused = 0, available = 1
               WHERE id = ? AND user_id = ?""",
            (product_id, user_id),
        ) as cur:
            await self._conn.commit()
            return cur.rowcount > 0

    async def update_price(self, product_id: int, new_price: int) -> None:
        await self._conn.execute(
            """UPDATE products
               SET current_price = ?, last_checked = datetime('now')
               WHERE id = ?""",
            (new_price, product_id),
        )
        await self._conn.execute(
            "INSERT INTO price_history (product_id, price) VALUES (?, ?)",
            (product_id, new_price),
        )
        await self._conn.commit()

    async def get_price_history(
        self, product_id: int, limit: int = 10
    ) -> list[dict]:
        async with self._conn.execute(
            """SELECT * FROM price_history
               WHERE product_id = ?
               ORDER BY recorded_at DESC
               LIMIT ?""",
            (product_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_all_active_products(self) -> list[dict]:
        async with self._conn.execute(
            "SELECT * FROM products WHERE is_paused = 0 AND available = 1"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def count_user_products(self, user_id: int) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM products WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return row[0]

    async def update_last_checked(self, product_id: int) -> None:
        """Touch last_checked without changing price (price unchanged on this check)."""
        await self._conn.execute(
            "UPDATE products SET last_checked = datetime('now') WHERE id = ?",
            (product_id,),
        )
        await self._conn.commit()

    async def get_all_active_users(self) -> list[dict]:
        """Return all users with is_active=1 (for broadcast)."""
        async with self._conn.execute(
            "SELECT * FROM users WHERE is_active = 1"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def set_unavailable(self, product_id: int) -> None:
        await self._conn.execute(
            "UPDATE products SET available = 0 WHERE id = ?",
            (product_id,),
        )
        await self._conn.commit()

    async def set_user_inactive(self, user_id: int) -> None:
        await self._conn.execute(
            "UPDATE users SET is_active = 0 WHERE user_id = ?",
            (user_id,),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------ stats

    async def get_db_stats(self) -> dict:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_active = 1"
        ) as cur:
            total_users = (await cur.fetchone())[0]

        async with self._conn.execute(
            "SELECT COUNT(*) FROM products WHERE is_paused = 0 AND available = 1"
        ) as cur:
            total_products = (await cur.fetchone())[0]

        async with self._conn.execute(
            """SELECT COUNT(*) FROM price_history
               WHERE recorded_at >= datetime('now', '-1 day')"""
        ) as cur:
            checks_24h = (await cur.fetchone())[0]

        return {
            "total_users": total_users,
            "total_products": total_products,
            "checks_24h": checks_24h,
        }

    async def get_user_stats(self, user_id: int) -> dict:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM products WHERE user_id = ?", (user_id,)
        ) as cur:
            total = (await cur.fetchone())[0]

        async with self._conn.execute(
            "SELECT COUNT(*) FROM products WHERE user_id = ? AND is_paused = 1",
            (user_id,),
        ) as cur:
            paused = (await cur.fetchone())[0]

        # biggest single-step price drop (negative value in kopecks)
        async with self._conn.execute(
            """SELECT MIN(h2.price - h1.price)
               FROM price_history h1
               JOIN price_history h2 ON h1.product_id = h2.product_id
               WHERE h2.recorded_at > h1.recorded_at
               AND h1.product_id IN (SELECT id FROM products WHERE user_id = ?)""",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            biggest_drop = row[0] if row and row[0] is not None else 0

        # most active product (most price changes)
        async with self._conn.execute(
            """SELECT product_id, COUNT(*) as changes
               FROM price_history
               WHERE product_id IN (SELECT id FROM products WHERE user_id = ?)
               GROUP BY product_id
               ORDER BY changes DESC
               LIMIT 1""",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            most_active_product_id = row[0] if row else None
            most_active_changes = row[1] if row else 0

        # total_savings: sum of all negative price deltas, computed in Python
        async with self._conn.execute(
            """SELECT product_id, price
               FROM price_history
               WHERE product_id IN (SELECT id FROM products WHERE user_id = ?)
               ORDER BY product_id, recorded_at""",
            (user_id,),
        ) as cur:
            history_rows = await cur.fetchall()

        total_savings = 0
        prev_by_product: dict[int, int] = {}
        for row in history_rows:
            pid, price = row[0], row[1]
            if pid in prev_by_product:
                delta = price - prev_by_product[pid]
                if delta < 0:
                    total_savings += abs(delta)
            prev_by_product[pid] = price

        return {
            "total": total,
            "paused": paused,
            "biggest_drop": biggest_drop,          # kopecks, negative = drop
            "most_active_product_id": most_active_product_id,
            "most_active_changes": most_active_changes,
            "total_savings": total_savings,         # kopecks
        }

    # --------------------------------------------------------------- history

    async def cleanup_history(self) -> int:
        async with self._conn.execute(
            "DELETE FROM price_history WHERE recorded_at < datetime('now', '-90 days')"
        ) as cur:
            await self._conn.commit()
            return cur.rowcount


# Module-level singleton — used by handlers, scheduler, etc.
from config import settings  # noqa: E402

db = Database(settings.DATABASE_PATH)
