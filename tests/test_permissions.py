"""Ownership, admin, CSRF and disabled-account gates (C3.I1)."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from httpx import AsyncClient

from yourss import accounts

if TYPE_CHECKING:
    from yourss.db import Database

SeedUser = Callable[..., Awaitable[int]]


def unique(prefix: str = "perm") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def login(client: AsyncClient, user_id: int, secret: str, *, version: int = 1) -> None:
    client.cookies.set(accounts.SESSION_COOKIE, accounts.create_session_cookie(secret, user_id, version, 3600))


async def test_public_pages_are_read_from_the_database(
    client: AsyncClient, db: "Database", seed_user: SeedUser
) -> None:
    username = unique()
    user_id = await seed_user(username)
    publish = unique("pub")
    cursor = await db.execute(
        "INSERT INTO pages (user_id, name, publish, created_at, updated_at) VALUES (?, 'Home', ?, ?, ?)",
        (user_id, publish, accounts.iso_now(), accounts.iso_now()),
    )
    page_id = int(cursor.lastrowid or 0)
    await db.execute(
        "INSERT INTO subscriptions (page_id, ref, kind, canonical_id, created_at) VALUES (?, ?, 'channel', ?, ?)",
        (page_id, "UC" + "f" * 22, "UC" + "f" * 22, accounts.iso_now()),
    )
    await db.commit()
    tree = await client.get(f"/u/{username}")
    assert tree.status_code == 200 and "Home" in tree.text
    public = await client.get(f"/p/{publish}")
    assert public.status_code == 200


async def test_authentication_and_admin_are_required(client: AsyncClient, seed_user: SeedUser, secret_key: str) -> None:
    assert (await client.get("/account")).status_code == 401
    user_id = await seed_user(unique())
    login(client, user_id, secret_key)
    assert (await client.get("/admin/users")).status_code == 403


async def test_csrf_is_required_for_mutations(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    user_id = await seed_user(unique())
    login(client, user_id, secret_key)
    await client.get("/account")
    response = await client.post("/account/pages", data={"name": unique("Page")})
    assert response.status_code == 403
    assert await db.fetchone("SELECT 1 FROM pages WHERE user_id = ?", (user_id,)) is None


async def test_disabled_or_stale_session_is_rejected(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    user_id = await seed_user(unique(), disabled=True)
    login(client, user_id, secret_key)
    assert (await client.get("/account")).status_code == 401

    user_id = await seed_user(unique())
    login(client, user_id, secret_key, version=1)
    await db.execute("UPDATE users SET session_version = 2 WHERE id = ?", (user_id,))
    await db.commit()
    assert (await client.get("/account")).status_code == 401


async def test_last_admin_cannot_delete_own_account(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    admin_id = await seed_user(unique("root"), role="admin")
    await db.execute("DELETE FROM users WHERE role = 'admin' AND id != ?", (admin_id,))
    await db.commit()
    login(client, admin_id, secret_key)
    token = str(client.cookies.get(accounts.CSRF_COOKIE) or "")
    assert (await client.get("/account")).status_code == 200
    token = str(client.cookies.get(accounts.CSRF_COOKIE) or token)
    response = await client.post("/account/delete", data={"confirm": "DELETE", "csrf_token": token})
    assert response.status_code == 400
    assert await db.fetchone("SELECT 1 FROM users WHERE id = ?", (admin_id,)) is not None
