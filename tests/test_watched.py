"""Server-side watched state and its API (C4.I1)."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from httpx import AsyncClient

from yourss import accounts, watched

if TYPE_CHECKING:
    from yourss.db import Database

SeedUser = Callable[..., Awaitable[int]]


def login(client: AsyncClient, user_id: int, secret: str) -> None:
    client.cookies.set(accounts.SESSION_COOKIE, accounts.create_session_cookie(secret, user_id, 1, 3600))


async def csrf(client: AsyncClient) -> str:
    await client.get("/account")
    return str(client.cookies[accounts.CSRF_COOKIE])


async def test_add_remove_and_cap(db: "Database", seed_user: SeedUser) -> None:
    user_id = await seed_user("watcher")
    await watched.set_watched(db, user_id, add=["a", "b"], now=100)
    assert await watched.get_watched(db, user_id) == {"a": 100, "b": 100}
    await watched.set_watched(db, user_id, remove=["a"], now=200)
    assert await watched.get_watched(db, user_id) == {"b": 100}
    await watched.set_watched(db, user_id, add=[f"v{i}" for i in range(watched.WATCHED_MAX + 5)], now=300)
    assert len(await watched.get_watched(db, user_id)) == watched.WATCHED_MAX


async def test_import_once_and_ms_to_seconds(db: "Database", seed_user: SeedUser) -> None:
    user_id = await seed_user("importer")
    first = await watched.import_watched(db, user_id, {"old": 1_700_000_000_000, "new": 1_700_000_100}, now=400)
    assert first == {"old": 1_700_000_000, "new": 1_700_000_100}
    marker = await db.fetchone("SELECT watched_imported_at FROM users WHERE id = ?", (user_id,))
    assert marker is not None and marker[0] is not None
    second = await watched.import_watched(db, user_id, {"ignored": 1}, now=500)
    assert "ignored" not in second


async def test_api_requires_login_and_csrf(
    client: AsyncClient, seed_user: SeedUser, secret_key: str
) -> None:
    assert (await client.get("/api/watched")).status_code == 401
    user_id = await seed_user("apiwatcher")
    login(client, user_id, secret_key)
    token = await csrf(client)
    assert (await client.get("/api/watched")).json() == {}
    response = await client.post(
        "/api/watched", json={"add": ["v1"], "remove": []}, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 200 and set(response.json()) == {"v1"}
    rejected = await client.post("/api/watched", json={"add": ["v2"], "remove": []})
    assert rejected.status_code == 403
    imported = await client.post(
        "/api/watched/import",
        json={"watched": {"v3": 1_700_000_000_000}},
        headers={"X-CSRF-Token": token},
    )
    assert imported.status_code == 200 and imported.json()["v3"] == 1_700_000_000
    assert imported.headers["cache-control"] == "no-store"
    assert "Cookie" in imported.headers["vary"]


async def test_import_map_cap_is_rejected(client: AsyncClient, seed_user: SeedUser, secret_key: str) -> None:
    user_id = await seed_user("bounded")
    login(client, user_id, secret_key)
    token = await csrf(client)
    oversized = {"watched": {f"v{i}": 1 for i in range(3001)}}
    response = await client.post("/api/watched/import", json=oversized, headers={"X-CSRF-Token": token})
    assert response.status_code == 422
