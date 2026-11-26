"""Private mode and noindex headers (C5.I1)."""

from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from pytest import MonkeyPatch

from yourss import accounts
from yourss.settings import current_config


def unique(prefix: str = "private") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def login(client: AsyncClient, user_id: int, secret: str) -> None:
    client.cookies.set(accounts.SESSION_COOKIE, accounts.create_session_cookie(secret, user_id, 1, 3600))


async def test_root_sends_a_signed_in_account_to_its_page(client: AsyncClient, seed_user: Any, secret_key: str) -> None:
    name = unique("root")
    user_id = await seed_user(name)
    login(client, user_id, secret_key)
    resp = await client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/u/{name}"


async def test_private_mode_gates_reads_and_keeps_auth_reachable(client: AsyncClient, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(current_config, "private", True)
    home = await client.get("/")
    assert home.status_code == 303
    assert home.headers["location"].startswith("/login?next=")
    user = await client.get(f"/u/{unique()}")
    assert user.status_code == 303
    assert (await client.get("/login")).status_code == 200
    assert (await client.get("/auth/verify?token=bad")).status_code == 200
    assert (await client.get("/api/version")).status_code == 200
    assert (await client.get("/static/yourss.css")).status_code == 200


async def test_private_mode_htmx_and_api_are_401(client: AsyncClient, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(current_config, "private", True)
    htmx = await client.get("/htmx/folders", headers={"HX-Request": "true"})
    assert htmx.status_code == 401
    assert htmx.headers["hx-refresh"] == "true"
    api = await client.get("/api/watched")
    assert api.status_code == 401


async def test_private_mode_allows_a_signed_in_account(
    client: AsyncClient, seed_user: Any, secret_key: str, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(current_config, "private", True)
    user_id = await seed_user(unique())
    login(client, user_id, secret_key)
    home = await client.get("/")
    assert home.status_code == 303
    assert home.headers["location"].startswith("/u/")


async def test_noindex_headers_on_private_and_account_pages(client: AsyncClient, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(current_config, "private", False)
    monkeypatch.setattr(current_config, "noindex", False)
    assert (await client.get("/login")).headers["x-robots-tag"] == "noindex, nofollow"
    assert (await client.get("/account")).headers["x-robots-tag"] == "noindex, nofollow"
    assert "x-robots-tag" not in (await client.get("/")).headers

    monkeypatch.setattr(current_config, "noindex", True)
    assert (await client.get("/")).headers["x-robots-tag"] == "noindex, nofollow"
