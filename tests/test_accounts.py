"""Account self-service: profile, export, pages and deletion (C3.I1)."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from httpx import AsyncClient

from yourss import accounts

if TYPE_CHECKING:
    from yourss.db import Database

SeedUser = Callable[..., Awaitable[int]]


def unique(prefix: str = "acct") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def login(client: AsyncClient, user_id: int, secret: str, version: int = 1) -> None:
    client.cookies.set(
        accounts.SESSION_COOKIE,
        accounts.create_session_cookie(secret, user_id, version, 3600),
    )


async def csrf(client: AsyncClient, path: str = "/account") -> str:
    response = await client.get(path)
    assert response.status_code == 200
    return str(client.cookies[accounts.CSRF_COOKIE])


async def test_profile_update_export_and_delete(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    username = unique()
    user_id = await seed_user(username)
    login(client, user_id, secret_key)
    token = await csrf(client)
    response = await client.post(
        "/account",
        data={"username": f"{username}-new", "display_name": "New Name", "csrf_token": token},
    )
    assert response.status_code == 303
    row = await db.fetchone("SELECT username, display_name FROM users WHERE id = ?", (user_id,))
    assert row is not None and tuple(row) == (f"{username}-new", "New Name")

    exported = await client.get("/account/export")
    assert exported.status_code == 200
    assert exported.json()["user"]["username"] == f"{username}-new"
    assert exported.json()["pages"] == []

    missing = await client.post("/account/delete", data={"confirm": "", "csrf_token": token})
    assert missing.status_code == 400
    deleted = await client.post("/account/delete", data={"confirm": "DELETE", "csrf_token": token})
    assert deleted.status_code == 303
    assert await db.fetchone("SELECT 1 FROM users WHERE id = ?", (user_id,)) is None


async def test_page_crud_from_the_account(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    user_id = await seed_user(unique())
    login(client, user_id, secret_key)
    token = await csrf(client)
    page = unique("Page")
    response = await client.post(
        "/account/pages",
        data={"name": page, "publish": "", "warmup": "on", "csrf_token": token},
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/account/pages/{page}"
    assert await db.fetchone("SELECT 1 FROM pages WHERE user_id = ? AND name = ?", (user_id, page)) is not None

    editor = await client.get(f"/account/pages/{page}")
    assert editor.status_code == 200
    assert page in editor.text

    renamed = page + " Renamed"
    response = await client.post(
        f"/account/pages/{page}",
        data={"new_name": renamed, "publish": unique("pub"), "warmup": "", "csrf_token": token},
    )
    assert response.status_code == 303
    row = await db.fetchone("SELECT name, warmup FROM pages WHERE user_id = ?", (user_id,))
    assert row is not None and row["name"] == renamed and row["warmup"] == 0

    response = await client.post(f"/account/pages/{renamed}/delete", data={"csrf_token": token})
    assert response.status_code == 303
    assert await db.fetchone("SELECT 1 FROM pages WHERE user_id = ?", (user_id,)) is None


async def test_add_and_remove_subscription_from_the_account(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    user_id = await seed_user(unique())
    login(client, user_id, secret_key)
    token = await csrf(client)
    page = unique("Subs")
    await client.post("/account/pages", data={"name": page, "csrf_token": token})
    response = await client.post(
        f"/account/pages/{page}/subscriptions",
        data={"ref": "UC" + "a" * 22, "csrf_token": token},
    )
    assert response.status_code == 303
    page_row = await db.fetchone("SELECT id FROM pages WHERE user_id = ? AND name = ?", (user_id, page))
    assert page_row is not None
    assert await db.fetchone("SELECT 1 FROM subscriptions WHERE page_id = ?", (int(page_row["id"]),)) is not None
    subscription = await db.fetchone("SELECT id FROM subscriptions WHERE page_id = ?", (int(page_row["id"]),))
    assert subscription is not None
    response = await client.post(
        f"/account/pages/{page}/subscriptions/{int(subscription['id'])}/delete",
        data={"csrf_token": token},
    )
    assert response.status_code == 303
    assert await db.fetchone("SELECT 1 FROM subscriptions WHERE page_id = ?", (int(page_row["id"]),)) is None
