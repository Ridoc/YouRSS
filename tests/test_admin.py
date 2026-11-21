"""Admin user management and warm-up status (C3.I1)."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from httpx import AsyncClient
from pytest import MonkeyPatch

from yourss import accounts, mail

if TYPE_CHECKING:
    from yourss.db import Database

SeedUser = Callable[..., Awaitable[int]]


def unique(prefix: str = "admin") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def login(client: AsyncClient, user_id: int, secret: str, *, version: int = 1) -> None:
    client.cookies.set(accounts.SESSION_COOKIE, accounts.create_session_cookie(secret, user_id, version, 3600))


async def csrf(client: AsyncClient, path: str = "/admin/users") -> str:
    response = await client.get(path)
    assert response.status_code == 200
    return str(client.cookies[accounts.CSRF_COOKIE])


class RecordingMailer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, text: str) -> None:
        self.messages.append((to, subject, text))


async def test_admin_manages_users(
    client: AsyncClient,
    db: "Database",
    seed_user: SeedUser,
    secret_key: str,
    monkeypatch: MonkeyPatch,
) -> None:
    admin_id = await seed_user(unique("root"), role="admin")
    user_id = await seed_user(unique("member"), email=f"{unique()}@example.com")
    login(client, admin_id, secret_key)
    token = await csrf(client)

    page = await client.get("/admin/users")
    assert page.status_code == 200 and "Users" in page.text

    disabled = await client.post(f"/admin/users/{user_id}/disable", data={"csrf_token": token})
    assert disabled.status_code == 303
    row = await db.fetchone("SELECT disabled_at, session_version FROM users WHERE id = ?", (user_id,))
    assert row is not None and row["disabled_at"] is not None and row["session_version"] == 2

    enabled = await client.post(f"/admin/users/{user_id}/enable", data={"csrf_token": token})
    assert enabled.status_code == 303
    enabled_row = await db.fetchone("SELECT disabled_at FROM users WHERE id = ?", (user_id,))
    assert enabled_row is not None and enabled_row["disabled_at"] is None

    email = f"{unique()}@example.com"
    set_email = await client.post(f"/admin/users/{user_id}/email", data={"email": email, "csrf_token": token})
    assert set_email.status_code == 303
    email_row = await db.fetchone("SELECT email FROM users WHERE id = ?", (user_id,))
    assert email_row is not None and email_row["email"] == email

    mailer = RecordingMailer()
    monkeypatch.setattr(mail, "get_mailer", lambda settings: mailer)
    sent = await client.post(f"/admin/users/{user_id}/send-link", data={"csrf_token": token})
    assert sent.status_code == 303
    assert mailer.messages and mailer.messages[0][0] == email

    deleted = await client.post(f"/admin/users/{user_id}/delete", data={"csrf_token": token})
    assert deleted.status_code == 303
    assert await db.fetchone("SELECT 1 FROM users WHERE id = ?", (user_id,)) is None


async def test_admin_cannot_demote_or_delete_self(client: AsyncClient, seed_user: SeedUser, secret_key: str) -> None:
    admin_id = await seed_user(unique("root"), role="admin")
    login(client, admin_id, secret_key)
    token = await csrf(client)
    response = await client.post(f"/admin/users/{admin_id}/role", data={"role": "user", "csrf_token": token})
    assert response.status_code == 400
    response = await client.post(f"/admin/users/{admin_id}/delete", data={"csrf_token": token})
    assert response.status_code == 400


async def test_status_page(client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str) -> None:
    admin_id = await seed_user(unique("root"), role="admin")
    login(client, admin_id, secret_key)
    await db.execute(
        "INSERT INTO warmup_status (channel, state, checked_at) VALUES (?, 'live', ?)",
        ("UC" + "e" * 22, accounts.iso_now()),
    )
    await db.commit()
    response = await client.get("/admin/status")
    assert response.status_code == 200 and "Warm-up status" in response.text
