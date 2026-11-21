"""Magic-link auth: tokens, verify flow, CSRF, rate limits, sessions and admin bootstrap (C2.I1)."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, Response
from pydantic import SecretStr
from pytest import MonkeyPatch, mark, raises

from tests.conftest import request_with
from yourss import accounts, captcha, mail, ratelimit
from yourss.settings import current_config

if TYPE_CHECKING:
    from yourss.db import Database

BASE = "https://rss.example"
SeedUser = Callable[..., Awaitable[int]]


class RecordingMailer:
    """Captures what the router asks to send; the Mailer protocol is one async `send`."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, text: str) -> None:
        self.messages.append((to, subject, text))


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def form_field(html: str, action: str, name: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one(f"form[action='{action}']")
    assert form is not None, f"no form posting to {action}"
    field = form.select_one(f"input[name={name}]")
    assert field is not None, f"no {name} field in the form posting to {action}"
    return str(field["value"])


async def login_csrf(client: AsyncClient) -> str:
    resp = await client.get("/login")
    assert resp.status_code == 200
    return form_field(resp.text, "/auth/request", "csrf_token")


async def verify_csrf(client: AsyncClient, raw: str) -> str:
    resp = await client.get("/auth/verify", params={"token": raw})
    assert resp.status_code == 200
    return form_field(resp.text, "/auth/verify", "csrf_token")


async def magic_token(db: "Database", seed_user: SeedUser, prefix: str) -> tuple[str, str]:
    username = unique(prefix)
    email = f"{username}@example.com"
    await seed_user(username, email=email)
    return email, await accounts.create_login_token(db, email)


def cookie_header(resp: Response, name: str) -> str:
    headers = [header for header in resp.headers.get_list("set-cookie") if header.startswith(f"{name}=")]
    assert headers, f"no {name} cookie in {resp.headers.get_list('set-cookie')}"
    return headers[0]


# Tokens -----------------------------------------------------------------------------------------


def test_normalize_email() -> None:
    assert accounts.normalize_email("  Alice@Example.COM ") == "alice@example.com"
    with raises(ValueError):
        accounts.normalize_email("not-an-email")


async def test_login_token_stores_only_its_hash(db: "Database") -> None:
    email = f"{unique('hash')}@example.com"
    raw = await accounts.create_login_token(db, email)
    row = await db.fetchone("SELECT token_hash, used_at, expires_at FROM login_tokens WHERE email = ?", (email,))
    assert row is not None
    assert row["token_hash"] == accounts.hash_token(raw)
    assert row["token_hash"] != raw
    assert len(str(row["token_hash"])) == 64
    assert row["used_at"] is None


async def test_login_token_is_single_use(db: "Database") -> None:
    email = f"{unique('once')}@example.com"
    raw = await accounts.create_login_token(db, email)
    assert await accounts.consume_login_token(db, raw) == email
    assert await accounts.consume_login_token(db, raw) is None


async def test_login_token_expires(db: "Database") -> None:
    email = f"{unique('expiry')}@example.com"
    fresh = await accounts.create_login_token(db, email, now=1000, ttl=60)
    assert await accounts.consume_login_token(db, fresh, now=1059) == email
    stale = await accounts.create_login_token(db, email, now=1000, ttl=60)
    assert await accounts.consume_login_token(db, stale, now=1061) is None


# GET then POST verify ---------------------------------------------------------------------------


async def test_get_verify_does_not_consume_then_post_consumes(
    client: AsyncClient, db: "Database", seed_user: SeedUser
) -> None:
    _, raw = await magic_token(db, seed_user, "verify")
    page = await client.get("/auth/verify", params={"token": raw})
    assert page.status_code == 200
    assert form_field(page.text, "/auth/verify", "token") == raw

    # Email scanners follow the link: the GET must leave the token usable
    row = await db.fetchone("SELECT used_at FROM login_tokens WHERE token_hash = ?", (accounts.hash_token(raw),))
    assert row is not None and row["used_at"] is None

    resp = await client.post(
        "/auth/verify",
        data={"token": raw, "csrf_token": form_field(page.text, "/auth/verify", "csrf_token")},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/account"
    assert cookie_header(resp, accounts.SESSION_COOKIE)
    row = await db.fetchone("SELECT used_at FROM login_tokens WHERE token_hash = ?", (accounts.hash_token(raw),))
    assert row is not None and row["used_at"] is not None

    # A second POST with the same link is refused; the anonymous CSRF token no longer matches the session
    again = await client.post(
        "/auth/verify",
        data={"token": raw, "csrf_token": form_field(page.text, "/auth/verify", "csrf_token")},
    )
    assert again.status_code == 403
    assert accounts.SESSION_COOKIE not in again.cookies


async def test_session_cookie_flags(
    client: AsyncClient, db: "Database", seed_user: SeedUser, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(current_config, "public_base_url", None)
    _, raw = await magic_token(db, seed_user, "flags")
    page = await client.get("http://test/auth/verify", params={"token": raw})
    token = form_field(page.text, "/auth/verify", "csrf_token")
    resp = await client.post("http://test/auth/verify", data={"token": raw, "csrf_token": token})
    assert resp.status_code == 303
    header = cookie_header(resp, accounts.SESSION_COOKIE).lower()
    assert "path=/" in header
    assert "httponly" in header
    assert "samesite=lax" in header
    assert f"max-age={int(current_config.session_ttl.total_seconds())}" in header
    # Plain HTTP and no https base: not Secure
    assert "secure" not in header


async def test_session_cookie_is_secure_behind_https(client: AsyncClient, db: "Database", seed_user: SeedUser) -> None:
    _, raw = await magic_token(db, seed_user, "https")
    page = await client.get("https://test/auth/verify", params={"token": raw})
    token = form_field(page.text, "/auth/verify", "csrf_token")
    resp = await client.post("https://test/auth/verify", data={"token": raw, "csrf_token": token})
    assert resp.status_code == 303
    assert "secure" in cookie_header(resp, accounts.SESSION_COOKIE).lower()


@mark.parametrize("target", ["//evil.com", "https://evil.com", "javascript:alert(1)"])
async def test_verify_rejects_a_next_target_outside_the_site(
    client: AsyncClient, db: "Database", seed_user: SeedUser, target: str
) -> None:
    _, raw = await magic_token(db, seed_user, "nextbad")
    resp = await client.post(
        "/auth/verify",
        data={"token": raw, "csrf_token": await verify_csrf(client, raw), "next": target},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/account"


async def test_verify_accepts_a_local_next_target(client: AsyncClient, db: "Database", seed_user: SeedUser) -> None:
    _, raw = await magic_token(db, seed_user, "nextok")
    resp = await client.post(
        "/auth/verify",
        data={"token": raw, "csrf_token": await verify_csrf(client, raw), "next": "/account?tab=pages"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/account?tab=pages"


async def test_concurrent_verify_has_exactly_one_winner(
    client: AsyncClient, db: "Database", seed_user: SeedUser
) -> None:
    _, raw = await magic_token(db, seed_user, "race")
    page = await client.get("/auth/verify", params={"token": raw})
    token = form_field(page.text, "/auth/verify", "csrf_token")
    form = {"token": raw, "csrf_token": token}
    first, second = await asyncio.gather(
        client.post("/auth/verify", data=form),
        client.post("/auth/verify", data=form),
    )
    winners = [resp for resp in (first, second) if resp.status_code == 303]
    assert len(winners) == 1
    assert cookie_header(winners[0], accounts.SESSION_COOKIE)
    loser = second if winners[0] is first else first
    assert loser.status_code != 303
    assert accounts.SESSION_COOKIE not in loser.cookies


# Requesting a link ------------------------------------------------------------------------------


async def test_request_sends_a_link_built_from_the_public_base(
    client: AsyncClient, db: "Database", seed_user: SeedUser, monkeypatch: MonkeyPatch
) -> None:
    username = unique("mail")
    email = f"{username}@example.com"
    await seed_user(username, email=email)
    monkeypatch.setattr(current_config, "public_base_url", BASE)
    fake = RecordingMailer()

    def fake_mailer(_: object) -> mail.Mailer:
        return fake

    monkeypatch.setattr(mail, "get_mailer", fake_mailer)
    resp = await client.post(
        "/auth/request",
        data={"email": email, "csrf_token": await login_csrf(client)},
        headers={"Host": "evil.example"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login?sent=1")
    assert len(fake.messages) == 1
    to, _, text = fake.messages[0]
    assert to == email
    assert "evil.example" not in text
    match = re.search(r"https://rss\.example/auth/verify\?token=([A-Za-z0-9_-]+)", text)
    assert match is not None
    raw = match.group(1)
    row = await db.fetchone("SELECT token_hash FROM login_tokens WHERE token_hash = ?", (accounts.hash_token(raw),))
    assert row is not None


async def test_request_requires_csrf(client: AsyncClient) -> None:
    missing = await client.post("/auth/request", data={"email": "nobody@example.com"})
    assert missing.status_code == 403
    invalid = await client.post(
        "/auth/request",
        data={"email": "nobody@example.com", "csrf_token": "not-a-token"},
    )
    assert invalid.status_code == 403


async def test_verify_requires_csrf(client: AsyncClient, db: "Database", seed_user: SeedUser) -> None:
    _, raw = await magic_token(db, seed_user, "verifycsrf")
    resp = await client.post("/auth/verify", data={"token": raw})
    assert resp.status_code == 403


async def test_request_is_rate_limited_per_ip(client: AsyncClient) -> None:
    token = await login_csrf(client)
    statuses = []
    for index in range(6):
        email = f"{unique(f'rate{index}')}@example.com"
        resp = await client.post("/auth/request", data={"email": email, "csrf_token": token})
        statuses.append(resp.status_code)
    assert statuses[:5] == [303] * 5
    assert statuses[5] == 429
    limited = await client.post("/auth/request", data={"email": f"{unique('more')}@example.com", "csrf_token": token})
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_rate_limiter_window_and_reset() -> None:
    limiter = ratelimit.RateLimiter(limit=2, window=60)
    assert limiter.allow("key", now=1000)
    assert limiter.allow("key", now=1000)
    assert not limiter.allow("key", now=1000)
    assert 0 < limiter.retry_after("key", now=1000) <= 60
    assert limiter.allow("key", now=1061)
    limiter.reset()
    assert limiter.retry_after("key", now=1061) == 0
    assert limiter.allow("key", now=1061)


# Registration and bootstrap ---------------------------------------------------------------------


async def test_request_does_not_create_an_account_when_registration_is_disabled(
    client: AsyncClient, db: "Database", seed_user: SeedUser, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(current_config, "registration_enabled", False)
    monkeypatch.setattr(current_config, "public_base_url", BASE)
    known = unique("known")
    await seed_user(known, email=f"{known}@example.com")
    unknown = f"{unique('unknown')}@example.com"
    token = await login_csrf(client)
    before = await db.fetchone("SELECT COUNT(*) AS count FROM users")
    assert before is not None

    known_resp = await client.post("/auth/request", data={"email": f"{known}@example.com", "csrf_token": token})
    unknown_resp = await client.post("/auth/request", data={"email": unknown, "csrf_token": token})
    assert known_resp.status_code == unknown_resp.status_code == 303
    assert known_resp.headers["location"] == unknown_resp.headers["location"]

    after = await db.fetchone("SELECT COUNT(*) AS count FROM users")
    assert after is not None and int(after["count"]) == int(before["count"])
    leaked = await db.fetchone("SELECT COUNT(*) AS count FROM login_tokens WHERE email = ?", (unknown,))
    assert leaked is not None and int(leaked["count"]) == 0


async def test_verify_does_not_create_an_account_when_registration_is_disabled(
    client: AsyncClient, db: "Database", monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(current_config, "registration_enabled", False)
    email = f"{unique('ghost')}@example.com"
    raw = await accounts.create_login_token(db, email)
    resp = await client.post(
        "/auth/verify",
        data={"token": raw, "csrf_token": await verify_csrf(client, raw)},
    )
    assert resp.status_code == 403
    assert accounts.SESSION_COOKIE not in resp.cookies
    row = await db.fetchone("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,))
    assert row is None


async def test_get_or_create_user_derives_a_valid_username(db: "Database") -> None:
    config = current_config.model_copy(update={"registration_enabled": True, "admin_email": None})
    email = f"newuser{uuid4().hex[:8]}@example.com"
    user = await accounts.get_or_create_user(db, email, config)
    assert user is not None
    assert user.email == email
    assert accounts.USERNAME_RE.match(user.username)
    again = await accounts.get_or_create_user(db, email, config)
    assert again is not None and again.id == user.id


async def test_get_or_create_user_returns_none_without_registration(db: "Database") -> None:
    config = current_config.model_copy(update={"registration_enabled": False})
    email = f"{unique('closed')}@example.com"
    assert await accounts.get_or_create_user(db, email, config) is None
    row = await db.fetchone("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,))
    assert row is None


async def test_first_verified_user_becomes_admin(db: "Database") -> None:
    await db.execute("DELETE FROM users")
    await db.commit()
    config = current_config.model_copy(update={"registration_enabled": True, "admin_email": None})
    email = f"{unique('first')}@example.com"
    user = await accounts.get_or_create_user(db, email, config)
    assert user is not None
    promoted = await accounts.promote_first_verified_user(db, user, config)
    assert promoted.role == "admin"
    row = await db.fetchone("SELECT role FROM users WHERE id = ?", (user.id,))
    assert row is not None and row["role"] == "admin"


async def test_non_empty_instance_never_promotes_a_stranger(db: "Database", seed_user: SeedUser) -> None:
    await seed_user(unique("existing"), email=f"{unique('existing')}@example.com")
    config = current_config.model_copy(update={"registration_enabled": True, "admin_email": None})
    user = await accounts.get_or_create_user(db, f"{unique('stranger')}@example.com", config)
    assert user is not None
    promoted = await accounts.promote_first_verified_user(db, user, config)
    assert promoted.role == "user"


async def test_admin_email_configured_does_not_promote_by_itself(db: "Database") -> None:
    await db.execute("DELETE FROM users")
    await db.commit()
    config = current_config.model_copy(
        update={"registration_enabled": True, "admin_email": "admin@example.com", "admin_username": "root"}
    )
    # A different account than ADMIN_EMAIL: startup bootstrap owns that one, verify-time does not promote
    user = await accounts.get_or_create_user(db, f"{unique('other')}@example.com", config)
    assert user is not None
    assert (await accounts.promote_first_verified_user(db, user, config)).role == "user"


async def test_magic_link_verify_clears_the_legacy_password_and_bumps_the_version(
    client: AsyncClient, db: "Database", seed_user: SeedUser
) -> None:
    username = unique("legacy")
    email = f"{username}@example.com"
    user_id = await seed_user(
        username,
        email=email,
        session_version=2,
        legacy_password="$argon2i$v=19$m=4096,t=3,p=1$c29tZXNhbHQ$hash",  # noqa: S106 - dummy fixture
        legacy_method="argon2",
    )
    raw = await accounts.create_login_token(db, email)
    resp = await client.post(
        "/auth/verify",
        data={"token": raw, "csrf_token": await verify_csrf(client, raw)},
    )
    assert resp.status_code == 303
    row = await db.fetchone(
        "SELECT legacy_password_hash, legacy_password_method, session_version FROM users WHERE id = ?", (user_id,)
    )
    assert row is not None
    assert row["legacy_password_hash"] is None
    assert row["legacy_password_method"] is None
    assert int(row["session_version"]) == 3


# Usernames --------------------------------------------------------------------------------------


async def test_derive_username_collision_gets_a_numeric_suffix(db: "Database", seed_user: SeedUser) -> None:
    base = f"du{uuid4().hex[:8]}"
    await seed_user(base, email=f"{base}@one.example")
    assert await accounts.derive_username(db, f"{base}@two.example") == f"{base}-2"
    await seed_user(f"{base}-2", email=f"{base}-2@one.example")
    assert await accounts.derive_username(db, f"{base}@three.example") == f"{base}-3"


async def test_derive_username_avoids_reserved_names_and_stays_valid(db: "Database") -> None:
    for name in ("admin", "api", "auth", "account", "login", "logout", "static", "healthz", "robots", "yourss"):
        derived = await accounts.derive_username(db, f"{name}@example.com")
        assert derived != name
        assert accounts.USERNAME_RE.match(derived)
    # A local part over the 32 character ceiling is truncated into a valid name
    long_name = await accounts.derive_username(db, f"{'x' * 40}@example.com")
    assert len(long_name) <= 32
    assert accounts.USERNAME_RE.match(long_name)


# CSRF helpers -----------------------------------------------------------------------------------


async def test_csrf_token_helper_roundtrip(fastapi_app: FastAPI) -> None:
    token, fresh = accounts.csrf_token_for(request_with(fastapi_app))
    assert token and fresh is True
    validation = request_with(fastapi_app, cookie=f"{accounts.CSRF_COOKIE}={token}", method="POST")
    accounts.validate_csrf(validation, token)
    with raises(HTTPException) as missing:
        accounts.validate_csrf(validation, None)
    assert missing.value.status_code == 403
    with raises(HTTPException) as invalid:
        accounts.validate_csrf(validation, "not-a-token")
    assert invalid.value.status_code == 403


# Logout -----------------------------------------------------------------------------------------


async def test_logout_clears_the_session_cookie(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    username = unique("logout")
    user_id = await seed_user(username, email=f"{username}@example.com")
    cookie = accounts.create_session_cookie(secret_key, user_id, 1, int(current_config.session_ttl.total_seconds()))
    client.cookies.set(accounts.SESSION_COOKIE, cookie, path="/", domain="test.local")
    page = await client.get("/")
    assert page.status_code == 200
    soup = BeautifulSoup(page.text, "html.parser")
    form = soup.select_one("form.yourss-logout[action='/logout']")
    assert form is not None
    field = form.select_one("input[name=csrf_token]")
    assert field is not None

    resp = await client.post("/logout", data={"csrf_token": str(field["value"])})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert "max-age=0" in cookie_header(resp, accounts.SESSION_COOKIE).lower()
    assert client.cookies.get(accounts.SESSION_COOKIE) in (None, "")
    after = await client.get("/")
    assert BeautifulSoup(after.text, "html.parser").select("form.yourss-logout[action='/logout']") == []


async def test_logout_requires_csrf(client: AsyncClient, seed_user: SeedUser, secret_key: str) -> None:
    username = unique("logoutcsrf")
    user_id = await seed_user(username, email=f"{username}@example.com")
    cookie = accounts.create_session_cookie(secret_key, user_id, 1, int(current_config.session_ttl.total_seconds()))
    client.cookies.set(accounts.SESSION_COOKIE, cookie, path="/", domain="test.local")
    resp = await client.post("/logout")
    assert resp.status_code == 403
    page = await client.get("/")
    assert BeautifulSoup(page.text, "html.parser").select("form.yourss-logout[action='/logout']")


# Login page and Turnstile -----------------------------------------------------------------------


async def test_login_page_offers_the_email_form_and_a_csrf_token(client: AsyncClient) -> None:
    resp = await client.get("/login")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.select_one("form[action='/auth/request']")
    assert form is not None
    assert form.select_one("input[name=email]")
    assert form.select_one("input[name=csrf_token]")
    assert form.select_one("input[name=next]")
    assert client.cookies.get(accounts.CSRF_COOKIE)
    assert "This page does not exist" not in resp.text


async def test_login_page_renders_turnstile_when_configured(client: AsyncClient, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(current_config, "turnstile_site_key", "test-site-key")
    resp = await client.get("/login")
    assert "cf-turnstile" in resp.text
    assert "test-site-key" in resp.text


async def test_request_fails_closed_when_turnstile_refuses(
    client: AsyncClient, db: "Database", seed_user: SeedUser, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(current_config, "turnstile_secret_key", SecretStr("secret"))
    monkeypatch.setattr(current_config, "public_base_url", BASE)
    username = unique("captcha")
    email = f"{username}@example.com"
    await seed_user(username, email=email)

    async def refuse(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(captcha, "verify_turnstile", refuse)
    resp = await client.post(
        "/auth/request",
        data={"email": email, "csrf_token": await login_csrf(client), "cf-turnstile-response": "bad"},
    )
    assert resp.status_code == 403
    row = await db.fetchone("SELECT COUNT(*) AS count FROM login_tokens WHERE email = ?", (email,))
    assert row is not None and int(row["count"]) == 0


async def test_request_passes_when_turnstile_accepts(
    client: AsyncClient, db: "Database", seed_user: SeedUser, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(current_config, "turnstile_secret_key", SecretStr("secret"))
    monkeypatch.setattr(current_config, "public_base_url", BASE)
    username = unique("captchaok")
    email = f"{username}@example.com"
    await seed_user(username, email=email)

    async def accept(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(captcha, "verify_turnstile", accept)
    resp = await client.post(
        "/auth/request",
        data={"email": email, "csrf_token": await login_csrf(client), "cf-turnstile-response": "good"},
    )
    assert resp.status_code == 303
    row = await db.fetchone("SELECT COUNT(*) AS count FROM login_tokens WHERE email = ?", (email,))
    assert row is not None and int(row["count"]) == 1
