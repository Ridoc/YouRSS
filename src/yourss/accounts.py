"""Accounts, magic-link tokens, sessions and CSRF for YouRSS+."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import time
from typing import TYPE_CHECKING

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, Request, Response
from loguru import logger
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

if TYPE_CHECKING:
    from .db import Database
    from .schema import AppSettings

SESSION_COOKIE = "yourss-session"
CSRF_COOKIE = "yourss-csrf"
LOGIN_TOKEN_TTL = 900
RESERVED_USERNAMES = {
    "admin",
    "api",
    "auth",
    "account",
    "login",
    "logout",
    "static",
    "healthz",
    "robots",
    "favicon",
    "u",
    "p",
    "root",
    "yourss",
}
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")


@dataclass(frozen=True)
class Account:
    id: int
    email: str | None
    username: str
    display_name: str
    role: str
    disabled_at: str | None
    session_version: int
    legacy_password_hash: str | None = None

    @classmethod
    def from_row(cls, row: object) -> Account:
        return cls(
            id=int(row["id"]),  # type: ignore[index]
            email=str(row["email"]) if row["email"] is not None else None,  # type: ignore[index]
            username=str(row["username"]),  # type: ignore[index]
            display_name=str(row["display_name"]),  # type: ignore[index]
            role=str(row["role"]),  # type: ignore[index]
            disabled_at=str(row["disabled_at"]) if row["disabled_at"] is not None else None,  # type: ignore[index]
            session_version=int(row["session_version"]),  # type: ignore[index]
            legacy_password_hash=(
                str(row["legacy_password_hash"]) if row["legacy_password_hash"] is not None else None  # type: ignore[index]
            ),
        )


def _now(value: float | None) -> int:
    return int(time() if value is None else value)


def iso_now(value: float | None = None) -> str:
    return datetime.fromtimestamp(_now(value), tz=UTC).isoformat(timespec="seconds")


def normalize_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as error:
        raise ValueError(str(error)) from error


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_ip(secret: str, value: str | None) -> str | None:
    if not value:
        return None
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


async def create_login_token(
    db: Database,
    email: str,
    *,
    now: float | None = None,
    ttl: int = LOGIN_TOKEN_TTL,
    ip: str | None = None,
    secret: str = "",
) -> str:
    """Store only the hash; return the raw token that goes into the link."""
    stamp = _now(now)
    raw = secrets.token_urlsafe(32)
    await db.execute(
        "INSERT INTO login_tokens (email, token_hash, purpose, expires_at, created_at, ip_hash) "
        "VALUES (?, ?, 'login', ?, ?, ?)",
        (email, hash_token(raw), stamp + ttl, stamp, hash_ip(secret, ip)),
    )
    await db.commit()
    return raw


async def consume_login_token(db: Database, raw: str, *, now: float | None = None) -> str | None:
    """Single use: the conditional UPDATE is the race winner; no read-then-write window."""
    stamp = _now(now)
    async with db.transaction():
        cursor = await db.execute(
            "UPDATE login_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
            (stamp, hash_token(raw), stamp),
        )
        try:
            if cursor.rowcount != 1:
                return None
        finally:
            await cursor.close()
        row = await db.fetchone("SELECT email FROM login_tokens WHERE token_hash = ?", (hash_token(raw),))
        return str(row["email"]) if row is not None else None


def _session_signature(secret: str, user_id: int, version: int, expiry: int) -> str:
    message = f"{user_id}|{version}|{expiry}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def create_session_cookie(
    secret: str, user_id: int, session_version: int, ttl: int, *, now: float | None = None
) -> str:
    expiry = _now(now) + ttl
    return f"{user_id}.{session_version}.{expiry}.{_session_signature(secret, user_id, session_version, expiry)}"


def verify_session_cookie(secret: str, cookie: str, *, now: float | None = None) -> tuple[int, int] | None:
    parts = cookie.split(".")
    if len(parts) != 4:
        return None
    try:
        user_id, version, expiry = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if expiry <= _now(now):
        return None
    expected = _session_signature(secret, user_id, version, expiry)
    if not hmac.compare_digest(parts[3].encode(), expected.encode()):
        return None
    return user_id, version


async def current_user(request: Request) -> Account | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    secret = getattr(request.app.state, "secret_key", None)
    db = getattr(request.app.state, "db", None)
    if not secret or db is None:
        return None
    verified = verify_session_cookie(str(secret), cookie)
    if verified is None:
        return None
    user_id, version = verified
    row = await db.fetchone(
        "SELECT id, email, username, display_name, role, disabled_at, session_version, legacy_password_hash "
        "FROM users WHERE id = ?",
        (user_id,),
    )
    if row is None or row["disabled_at"] is not None or int(row["session_version"]) != version:
        return None
    return Account.from_row(row)


async def require_user(request: Request) -> Account:
    user = await current_user(request)
    if user is None:
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Sign in required")
    return user


async def require_admin(request: Request) -> Account:
    user = await require_user(request)
    if user.role != "admin":
        raise HTTPException(HTTP_403_FORBIDDEN, "Admin required")
    return user


def _csrf_user_id(request: Request) -> int:
    user = getattr(request.state, "user", None)
    return int(user.id) if user is not None else 0


def _csrf_signature(secret: str, nonce: str, user_id: int = 0) -> str:
    message = f"{user_id}:{nonce}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _valid_csrf(secret: str, token: str, user_id: int = 0) -> bool:
    nonce, _, signature = token.partition(".")
    return bool(nonce and signature) and hmac.compare_digest(
        signature.encode(), _csrf_signature(secret, nonce, user_id).encode()
    )


def csrf_token_for(request: Request) -> tuple[str, bool]:
    secret = str(getattr(request.app.state, "secret_key", "") or "")
    user_id = _csrf_user_id(request)
    cookie = request.cookies.get(CSRF_COOKIE)
    if cookie and _valid_csrf(secret, cookie, user_id):
        return cookie, False
    nonce = secrets.token_urlsafe(24)
    return f"{nonce}.{_csrf_signature(secret, nonce, user_id)}", True


def set_csrf_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=60 * 60 * 24 * 30,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def validate_csrf(request: Request, form_token: str | None) -> None:
    secret = str(getattr(request.app.state, "secret_key", "") or "")
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not form_token or not hmac.compare_digest(cookie.encode(), form_token.encode()):
        raise HTTPException(HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    if not _valid_csrf(secret, form_token, _csrf_user_id(request)):
        raise HTTPException(HTTP_403_FORBIDDEN, "CSRF token invalid")


def session_ttl_seconds(ttl: object) -> int:
    return int(ttl.total_seconds())  # type: ignore[attr-defined]


def set_session_cookie(response: Response, secret: str, user: Account, ttl: object, *, secure: bool) -> None:
    max_age = session_ttl_seconds(ttl)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_cookie(secret, user.id, user.session_version, max_age),
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")


async def audit(
    db: Database, *, user_id: int | None, action: str, detail: str = "", ip_hash: str | None = None
) -> None:
    await db.execute(
        "INSERT INTO audit_log (user_id, action, detail, created_at, ip_hash) VALUES (?, ?, ?, ?, ?)",
        (user_id, action, detail, iso_now(), ip_hash),
    )
    await db.commit()


async def derive_username(db: Database, email: str, *, admin_username: str | None = None) -> str:
    base = admin_username or email.split("@", 1)[0]
    base = re.sub(r"[^a-z0-9_-]+", "-", base.lower()).strip("-_")
    if len(base) < 3:
        base = "user"
    base = base[:32]
    candidate = base
    suffix = 2
    while candidate in RESERVED_USERNAMES or not USERNAME_RE.match(candidate) or await _username_taken(db, candidate):
        tail = f"-{suffix}"
        candidate = base[: 32 - len(tail)] + tail
        suffix += 1
    return candidate


async def _username_taken(db: Database, username: str) -> bool:
    row = await db.fetchone("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,))
    return row is not None


async def get_or_create_user(
    db: Database, email: str, config: AppSettings, *, now: float | None = None
) -> Account | None:
    normalized = normalize_email(email)
    row = await db.fetchone(
        "SELECT id, email, username, display_name, role, disabled_at, session_version, legacy_password_hash "
        "FROM users WHERE email = ? COLLATE NOCASE",
        (normalized,),
    )
    if row is not None:
        return Account.from_row(row)
    if not config.registration_enabled:
        return None
    admin_match = config.admin_email is not None and normalized == normalize_email(str(config.admin_email))
    async with db.transaction():
        row = await db.fetchone("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (normalized,))
        if row is not None:
            row = await db.fetchone(
                "SELECT id, email, username, display_name, role, disabled_at, session_version, legacy_password_hash "
                "FROM users WHERE id = ?",
                (int(row["id"]),),
            )
            return Account.from_row(row) if row is not None else None
        username = await derive_username(db, normalized, admin_username=config.admin_username if admin_match else None)
        cursor = await db.execute(
            "INSERT INTO users (email, username, display_name, role, created_at) VALUES (?, ?, '', ?, ?)",
            (normalized, username, "admin" if admin_match else "user", iso_now(now)),
        )
        try:
            user_id = int(cursor.lastrowid)  # type: ignore[arg-type]
        finally:
            await cursor.close()
    return Account(user_id, normalized, username, "", "admin" if admin_match else "user", None, 1)


async def bootstrap_admin(db: Database, config: AppSettings) -> None:
    """Give ADMIN_EMAIL the admin role, adopting the imported user with ADMIN_USERNAME when present."""
    if config.admin_email is None:
        return
    email = normalize_email(str(config.admin_email))
    row = await db.fetchone("SELECT id, role FROM users WHERE email = ? COLLATE NOCASE", (email,))
    if row is not None:
        if str(row["role"]) != "admin":
            await db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (int(row["id"]),))
            await db.commit()
        return
    if config.admin_username:
        legacy = await db.fetchone(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE AND email IS NULL",
            (config.admin_username,),
        )
        if legacy is not None:
            await db.execute(
                "UPDATE users SET email = ?, role = 'admin' WHERE id = ?",
                (email, int(legacy["id"])),
            )
            await db.commit()
            return
    username = await derive_username(db, email, admin_username=config.admin_username)
    await db.execute(
        "INSERT INTO users (email, username, display_name, role, created_at) VALUES (?, ?, '', 'admin', ?)",
        (email, username, iso_now()),
    )
    await db.commit()


async def promote_first_verified_user(db: Database, user: Account, config: AppSettings) -> Account:
    if user.role == "admin" or user.disabled_at is not None:
        return user
    admin_match = config.admin_email is not None and user.email == normalize_email(str(config.admin_email))
    promote = admin_match
    if not promote and config.registration_enabled and config.admin_email is None:
        row = await db.fetchone("SELECT COUNT(*) AS count FROM users")
        promote = row is not None and int(row["count"]) == 1
    if promote:
        # WHY: on an open instance this is an unauthenticated first-claim window; make it loud.
        logger.warning("Promoting first verified user {} to admin (set YOURSS_ADMIN_EMAIL to pin it)", user.username)
        await db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (user.id,))
        await db.commit()
        return replace(user, role="admin")
    return user


async def clear_legacy_credentials(db: Database, user: Account) -> Account:
    """Claiming an imported account must invalidate its old password cookie."""
    if user.legacy_password_hash is None:
        return user
    await db.execute(
        "UPDATE users SET legacy_password_hash = NULL, legacy_password_method = NULL, "
        "session_version = session_version + 1 WHERE id = ?",
        (user.id,),
    )
    await db.commit()
    row = await db.fetchone(
        "SELECT id, email, username, display_name, role, disabled_at, session_version, legacy_password_hash "
        "FROM users WHERE id = ?",
        (user.id,),
    )
    return Account.from_row(row) if row is not None else user
