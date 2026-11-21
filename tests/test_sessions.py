"""Signed browser sessions: signature, expiry, session version and disabled accounts (C2.I1)."""

from collections.abc import Awaitable, Callable
from time import time
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import FastAPI

from tests.conftest import request_with
from yourss import accounts

if TYPE_CHECKING:
    from yourss.db import Database


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


SeedUser = Callable[..., Awaitable[int]]


def test_session_cookie_roundtrip_carries_the_version(secret_key: str) -> None:
    cookie = accounts.create_session_cookie(secret_key, user_id=42, session_version=7, ttl=600)
    assert accounts.verify_session_cookie(secret_key, cookie, now=time()) == (42, 7)


def test_session_cookie_rejects_tampering_and_another_key(secret_key: str) -> None:
    cookie = accounts.create_session_cookie(secret_key, user_id=42, session_version=1, ttl=600)
    tampered = cookie[:-1] + ("A" if cookie[-1] != "A" else "B")
    assert accounts.verify_session_cookie(secret_key, tampered, now=time()) is None
    assert accounts.verify_session_cookie("another-secret", cookie, now=time()) is None
    assert accounts.verify_session_cookie(secret_key, "garbage", now=time()) is None
    assert accounts.verify_session_cookie(secret_key, "", now=time()) is None


def test_session_cookie_expires(secret_key: str) -> None:
    cookie = accounts.create_session_cookie(secret_key, user_id=42, session_version=1, ttl=1)
    assert accounts.verify_session_cookie(secret_key, cookie, now=time() + 120) is None


async def test_session_version_invalidates_the_cookie(
    fastapi_app: FastAPI,
    db: "Database",
    seed_user: SeedUser,
    secret_key: str,
) -> None:
    username = unique("version")
    user_id = await seed_user(username, email=f"{username}@example.com", session_version=1)
    cookie = accounts.create_session_cookie(secret_key, user_id, 1, ttl=3600)
    request = request_with(fastapi_app, cookie=f"yourss-session={cookie}")
    account = await accounts.current_user(request)
    assert account is not None and account.id == user_id

    # Logout-all bumps the version: the cookie of every device is refused
    await db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user_id,))
    await db.commit()
    assert await accounts.current_user(request) is None


async def test_disabled_user_is_rejected(
    fastapi_app: FastAPI,
    seed_user: SeedUser,
    secret_key: str,
) -> None:
    username = unique("disabled")
    user_id = await seed_user(username, email=f"{username}@example.com", disabled=True)
    cookie = accounts.create_session_cookie(secret_key, user_id, 1, ttl=3600)
    request = request_with(fastapi_app, cookie=f"yourss-session={cookie}")
    assert await accounts.current_user(request) is None
