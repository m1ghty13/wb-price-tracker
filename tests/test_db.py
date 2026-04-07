import pytest
import pytest_asyncio

from db.database import Database
from parser.models import ProductInfo


# ------------------------------------------------------------------ fixture


@pytest_asyncio.fixture
async def db():
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


def _make_product(article: str = "12345678") -> ProductInfo:
    return ProductInfo(
        article=article,
        name="Test Product",
        current_price=10000,   # 100 RUB
        original_price=15000,  # 150 RUB
        discount_pct=33,
        url=f"https://wildberries.ru/catalog/{article}/detail.aspx",
        available=True,
    )


# ------------------------------------------------------------------ users


async def test_upsert_user_creates(db: Database):
    await db.upsert_user(111, "alice")
    products = await db.get_user_products(111)
    assert products == []  # user exists, no products yet


async def test_upsert_user_no_duplicate(db: Database):
    await db.upsert_user(111, "alice")
    await db.upsert_user(111, "alice_renamed")  # second call must not fail
    # count via count_user_products proxy — DB has one user row
    count = await db.count_user_products(111)
    assert count == 0  # still 0 products, 1 user row (no duplicate)


# --------------------------------------------------------------- add_product


async def test_add_product_success(db: Database):
    await db.upsert_user(1, "bob")
    product_id = await db.add_product(1, _make_product("11111111"))
    assert isinstance(product_id, int)
    assert product_id > 0


async def test_add_product_duplicate_returns_none(db: Database):
    await db.upsert_user(1, "bob")
    await db.add_product(1, _make_product("22222222"))
    result = await db.add_product(1, _make_product("22222222"))  # same article
    assert result is None


async def test_add_product_same_article_different_users(db: Database):
    """Same article for two different users must succeed."""
    await db.upsert_user(1, "bob")
    await db.upsert_user(2, "carol")
    id1 = await db.add_product(1, _make_product("33333333"))
    id2 = await db.add_product(2, _make_product("33333333"))
    assert id1 is not None
    assert id2 is not None
    assert id1 != id2


# ----------------------------------------------------------- delete_product


async def test_delete_product(db: Database):
    await db.upsert_user(1, "bob")
    pid = await db.add_product(1, _make_product("44444444"))

    deleted = await db.delete_product(pid, 1)
    assert deleted is True

    product = await db.get_product_by_id(pid, 1)
    assert product is None


async def test_delete_product_wrong_user(db: Database):
    """Cannot delete another user's product."""
    await db.upsert_user(1, "bob")
    await db.upsert_user(2, "carol")
    pid = await db.add_product(1, _make_product("55555555"))

    deleted = await db.delete_product(pid, 2)  # user 2 tries to delete user 1's product
    assert deleted is False


async def test_delete_product_cascades_history(db: Database):
    """Deleting a product must cascade-delete its price_history rows."""
    await db.upsert_user(1, "bob")
    pid = await db.add_product(1, _make_product("66666666"))

    await db.update_price(pid, 9000)
    await db.update_price(pid, 8000)

    history_before = await db.get_price_history(pid)
    assert len(history_before) == 2

    await db.delete_product(pid, 1)

    history_after = await db.get_price_history(pid)
    assert len(history_after) == 0


# ------------------------------------------------------------ update_price


async def test_update_price_updates_current_price(db: Database):
    await db.upsert_user(1, "bob")
    pid = await db.add_product(1, _make_product("77777777"))

    await db.update_price(pid, 8500)

    product = await db.get_product_by_id(pid, 1)
    assert product["current_price"] == 8500


async def test_update_price_writes_history(db: Database):
    await db.upsert_user(1, "bob")
    pid = await db.add_product(1, _make_product("88888888"))

    await db.update_price(pid, 9000)
    await db.update_price(pid, 7500)

    history = await db.get_price_history(pid)
    assert len(history) == 2
    prices = [h["price"] for h in history]
    assert 9000 in prices
    assert 7500 in prices


# ----------------------------------------------- get_all_active_products


async def test_get_all_active_products_excludes_paused(db: Database):
    await db.upsert_user(1, "bob")
    pid_active = await db.add_product(1, _make_product("10000001"))
    pid_paused = await db.add_product(1, _make_product("10000002"))

    await db.pause_product(pid_paused, 1)

    active = await db.get_all_active_products()
    active_ids = [p["id"] for p in active]

    assert pid_active in active_ids
    assert pid_paused not in active_ids


async def test_get_all_active_products_excludes_unavailable(db: Database):
    await db.upsert_user(1, "bob")
    pid_ok = await db.add_product(1, _make_product("10000003"))
    pid_unavail = await db.add_product(1, _make_product("10000004"))

    await db.set_unavailable(pid_unavail)

    active = await db.get_all_active_products()
    active_ids = [p["id"] for p in active]

    assert pid_ok in active_ids
    assert pid_unavail not in active_ids


# ------------------------------------------------- resume resets available


async def test_resume_resets_available(db: Database):
    await db.upsert_user(1, "bob")
    pid = await db.add_product(1, _make_product("10000005"))

    await db.set_unavailable(pid)
    product = await db.get_product_by_id(pid, 1)
    assert product["available"] == 0

    await db.resume_product(pid, 1)
    product = await db.get_product_by_id(pid, 1)
    assert product["available"] == 1
    assert product["is_paused"] == 0


# ------------------------------------------------------- count_user_products


async def test_count_user_products(db: Database):
    await db.upsert_user(1, "bob")
    assert await db.count_user_products(1) == 0

    await db.add_product(1, _make_product("20000001"))
    await db.add_product(1, _make_product("20000002"))
    assert await db.count_user_products(1) == 2
