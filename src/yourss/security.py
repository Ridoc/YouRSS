from __future__ import annotations

import asyncio
import hashlib
import hmac
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from secrets import compare_digest
from time import time
from typing import Protocol

import aiosqlite
from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic
from loguru import logger
from pydantic import SecretStr
from pydantic_yaml import parse_yaml_raw_as
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from .db import Database
from .ratelimit import login_ip_limiter
from .schema import Password, PasswordMethod, UsersConfig

security = HTTPBasic(auto_error=False)
argon2hasher = PasswordHasher()
# WHY: Argon2 is CPU-heavy; two concurrent verifications bound the damage of a password-spray request burst.
_verify_slots = asyncio.Semaphore(2)


class PasswordUser(Protocol):
    name: str
    password: Password | None


@dataclass
class DbPage:
    name: str
    publish: str | None
    warmup: bool
    channels: list[str]


@dataclass
class DbUser:
    id: int
    name: str
    email: str | None
    display_name: str
    role: str
    disabled_at: str | None
    session_version: int
    password: Password | None
    pages: list[DbPage]

    def page(self, name: str) -> DbPage | None:
        return next((page for page in self.pages if page.name == name), None)


def verify_password(user: PasswordUser, value: str) -> bool:
    if user.password is None:
        return False
    if user.password.method == PasswordMethod.CLEAR:
        # Bytes, not str: compare_digest refuses non-ASCII strings
        return compare_digest(user.password.value.get_secret_value().encode(), value.encode())
    if user.password.method == PasswordMethod.ARGON2:
        try:
            return argon2hasher.verify(user.password.value.get_secret_value(), value)
        except Exception:
            return False

    raise ValueError("Invalid password method")


async def verify_password_async(user: PasswordUser, value: str) -> bool:
    async with _verify_slots:
        return await asyncio.to_thread(verify_password, user, value)


def read_config() -> UsersConfig | None:
    """The users file; none without a file or when it cannot be read."""
    from .settings import current_config

    if current_config.users_file is None or not current_config.users_file.exists():
        return None
    try:
        return parse_yaml_raw_as(UsersConfig, current_config.users_file.read_bytes())
    except Exception as error:
        logger.exception(error)
        return None


# The signed login of a user behind a password, under its path (docs/specs/login.md §3)
LOGIN_COOKIE = "yourss-login"


def get_secret_key(request: Request) -> str:
    """The runtime signing key resolved once in the lifespan (env or generated DB key)."""
    return str(getattr(request.app.state, "secret_key", "") or "")


def login_signature(user: PasswordUser, expiry: int, secret: str) -> str:
    """HMAC of the user, expiry and password hash; the key itself never changes with the password."""
    if user.password is None:
        raise ValueError("Only a user behind a password logs in")
    key = hmac.new(secret.encode(), b"yourss-login", hashlib.sha256).digest()
    message = f"{user.name}|{expiry}|{user.password.value.get_secret_value()}".encode()
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return urlsafe_b64encode(digest).decode().rstrip("=")


def login_token(user: PasswordUser, secret: str, now: float | None = None) -> str:
    from .settings import current_config

    expiry = int((time() if now is None else now) + current_config.login_ttl.total_seconds())
    return f"{expiry}.{login_signature(user, expiry, secret)}"


def check_login(user: PasswordUser, token: str | None, secret: str, now: float | None = None) -> bool:
    if user.password is None or not token:
        return False
    expiry, _, signature = token.partition(".")
    if not expiry.isdigit() or int(expiry) <= (time() if now is None else now):
        return False
    return compare_digest(signature.encode(), login_signature(user, int(expiry), secret).encode())


class LoginRequired(Exception):
    """A user behind a password without a valid login: the application answers the login form (see `main.py`)."""

    def __init__(self, user: DbUser) -> None:
        super().__init__(user.name)
        self.user = user


class UnknownUser(Exception):
    """No user of this name: the application answers its 404 page (see `main.py`)."""


def _password_from_row(row: aiosqlite.Row) -> Password | None:
    hash_value = row["legacy_password_hash"]
    method = row["legacy_password_method"]
    if not hash_value or not method:
        return None
    try:
        return Password(method=PasswordMethod(str(method)), value=SecretStr(str(hash_value)))
    except ValueError:
        return None


async def _channels_for_page(db: Database, page_id: int) -> list[str]:
    rows = await db.fetchall(
        "SELECT ref FROM subscriptions WHERE page_id = ? ORDER BY position, id", (page_id,)
    )
    return [str(row["ref"]) for row in rows]


async def _load_user(request: Request, username: str) -> DbUser | None:
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT id, email, username, display_name, role, disabled_at, session_version, "
        "legacy_password_hash, legacy_password_method FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
    )
    if row is None:
        return None
    page_rows = await db.fetchall(
        "SELECT id, name, publish, warmup FROM pages WHERE user_id = ? ORDER BY position, name",
        (int(row["id"]),),
    )
    pages = [
        DbPage(
            name=str(page["name"]),
            publish=str(page["publish"]) if page["publish"] is not None else None,
            warmup=bool(page["warmup"]),
            channels=await _channels_for_page(db, int(page["id"])),
        )
        for page in page_rows
    ]
    return DbUser(
        id=int(row["id"]),
        name=str(row["username"]),
        email=str(row["email"]) if row["email"] is not None else None,
        display_name=str(row["display_name"]),
        role=str(row["role"]),
        disabled_at=str(row["disabled_at"]) if row["disabled_at"] is not None else None,
        session_version=int(row["session_version"]),
        password=_password_from_row(row),
        pages=pages,
    )


async def find_user(request: Request, username: str) -> DbUser | None:
    return await _load_user(request, username)


async def find_page(request: Request, page: str) -> DbPage | None:
    """A page published under this name; the others are only served inside their user."""
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT p.id, p.name, p.publish, p.warmup FROM pages p "
        "JOIN users u ON u.id = p.user_id "
        "WHERE p.publish = ? COLLATE NOCASE AND u.disabled_at IS NULL",
        (page,),
    )
    if row is None:
        return None
    return DbPage(
        name=str(row["name"]),
        publish=str(row["publish"]) if row["publish"] is not None else None,
        warmup=bool(row["warmup"]),
        channels=await _channels_for_page(db, int(row["id"])),
    )


async def get_auth_user(
    request: Request,
    user: DbUser | None = Depends(find_user),
    secret: str = Depends(get_secret_key),
) -> DbUser:
    """The user, once its login cookie or its Basic credentials are checked (docs/specs/login.md §5)."""
    if user is None or user.disabled_at is not None:
        raise UnknownUser()
    from . import accounts

    account = await accounts.current_user(request)
    if account is not None and account.id == user.id:
        return user

    if user.password is not None and not check_login(user, request.cookies.get(LOGIN_COOKIE), secret):
        credentials = await security(request)
        if credentials is None:
            raise LoginRequired(user)
        peer = request.client.host if request.client else ""
        if not login_ip_limiter.allow(peer):
            raise HTTPException(HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts")
        if not await verify_password_async(user, credentials.password):
            logger.warning("Wrong Basic password for {} from {}", user.name, peer)
            raise LoginRequired(user)

    return user
