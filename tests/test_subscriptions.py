"""Pages and subscriptions repository rules (C3.I1)."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from pytest import raises

from yourss.schema import AppSettings
from yourss.subscriptions import (
    DuplicateError,
    LimitError,
    SubscriptionError,
    add_subscription,
    create_page,
    delete_page,
    delete_subscription,
    get_page,
    list_pages,
    list_subscriptions,
    rename_page,
    reorder_subscriptions,
    set_publish,
    set_warmup,
)

if TYPE_CHECKING:
    from yourss.db import Database

SeedUser = Callable[..., Awaitable[int]]


def name(prefix: str = "page") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


async def test_page_crud_and_caps(db: "Database", seed_user: SeedUser) -> None:
    user_id = await seed_user(f"sub-{uuid4().hex[:8]}")
    settings = AppSettings(max_pages_per_user=1, max_page_subscriptions=2)
    page = await create_page(db, settings, user_id, name())
    assert (await get_page(db, user_id, str(page["name"]))) is not None
    assert [row["name"] for row in await list_pages(db, user_id)] == [page["name"]]
    with raises(LimitError):
        await create_page(db, settings, user_id, name())
    await delete_page(db, user_id, str(page["name"]))
    assert await list_pages(db, user_id) == []


async def test_names_publish_and_duplicates(db: "Database", seed_user: SeedUser) -> None:
    user_id = await seed_user(f"sub-{uuid4().hex[:8]}")
    page = await create_page(db, AppSettings(), user_id, name(), publish=name("pub"))
    with raises(DuplicateError):
        await create_page(db, AppSettings(), user_id, str(page["name"]))
    with raises(SubscriptionError):
        await create_page(db, AppSettings(), user_id, "bad/name")
    with raises(SubscriptionError):
        await create_page(db, AppSettings(), user_id, name(), publish="bad name")
    await rename_page(db, user_id, str(page["name"]), name("renamed"))
    with raises(SubscriptionError):
        await rename_page(db, user_id, "missing", name())


async def test_subscription_order_duplicate_and_delete(db: "Database", seed_user: SeedUser) -> None:
    user_id = await seed_user(f"sub-{uuid4().hex[:8]}")
    page = await create_page(db, AppSettings(), user_id, name())
    page_id = int(page["id"])
    first = await add_subscription(db, AppSettings(), page_id, "UC" + "a" * 22)
    second = await add_subscription(db, AppSettings(), page_id, "PL" + "b" * 22)
    assert [row["id"] for row in await list_subscriptions(db, page_id)] == [first["id"], second["id"]]
    with raises(DuplicateError):
        await add_subscription(db, AppSettings(), page_id, "UC" + "a" * 22)
    await reorder_subscriptions(db, page_id, [int(second["id"]), int(first["id"])])
    assert [row["id"] for row in await list_subscriptions(db, page_id)] == [second["id"], first["id"]]
    with raises(SubscriptionError):
        await reorder_subscriptions(db, page_id, [int(first["id"])])
    await delete_subscription(db, page_id, int(first["id"]))
    assert len(await list_subscriptions(db, page_id)) == 1


async def test_subscription_cap_and_publish_toggle(db: "Database", seed_user: SeedUser) -> None:
    user_id = await seed_user(f"sub-{uuid4().hex[:8]}")
    settings = AppSettings(max_page_subscriptions=1)
    page = await create_page(db, settings, user_id, name())
    page_id = int(page["id"])
    await add_subscription(db, settings, page_id, "UC" + "c" * 22)
    with raises(LimitError):
        await add_subscription(db, settings, page_id, "UC" + "d" * 22)
    await set_publish(db, user_id, str(page["name"]), name("pub"))
    published = await get_page(db, user_id, str(page["name"]))
    assert published is not None and published["publish"] is not None
    await set_warmup(db, user_id, str(page["name"]), False)
    cooled = await get_page(db, user_id, str(page["name"]))
    assert cooled is not None and cooled["warmup"] == 0
