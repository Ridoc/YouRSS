"""Instance administration: users, roles, email links and warm-up status."""

from __future__ import annotations

import sqlite3
from typing import Annotated
from urllib.parse import quote

import aiosqlite
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER, HTTP_404_NOT_FOUND

from .. import accounts, mail
from ..settings import current_config
from .jinja import template_page

router = APIRouter()


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=HTTP_303_SEE_OTHER)


def _error(request: Request, message: str, *, status: int = 400) -> HTMLResponse:
    return template_page(
        request,
        "pages/error.jinja-html",
        status_code=status,
        title="Admin",
        message=message,
        home_link=True,
    )


async def _audit(request: Request, admin: accounts.Account, action: str, detail: str = "") -> None:
    await accounts.audit(
        request.app.state.db,
        user_id=admin.id,
        action=action,
        detail=detail,
        ip_hash=accounts.hash_ip(request.app.state.secret_key, accounts.client_ip(request)),
    )


async def _admin_count(request: Request) -> int:
    row = await request.app.state.db.fetchone("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    return int(row[0]) if row is not None else 0


async def _user_row(request: Request, user_id: int) -> aiosqlite.Row | None:
    row: aiosqlite.Row | None = await request.app.state.db.fetchone(
        "SELECT id, email, username, display_name, role, created_at, disabled_at, session_version "
        "FROM users WHERE id = ?",
        (user_id,),
    )
    return row


@router.get("/admin/users", response_class=HTMLResponse)
async def users_page(request: Request, admin: accounts.Account = Depends(accounts.require_admin)) -> HTMLResponse:
    db = request.app.state.db
    rows = await db.fetchall(
        """
        SELECT u.id, u.email, u.username, u.display_name, u.role, u.created_at, u.disabled_at,
               (SELECT COUNT(*) FROM pages p WHERE p.user_id = u.id) AS page_count,
               (SELECT COUNT(*) FROM watched w WHERE w.user_id = u.id) AS watched_count
        FROM users u ORDER BY u.role DESC, u.username
        """
    )
    return template_page(
        request,
        "pages/admin-users.jinja-html",
        title="Users",
        admin=admin,
        users=rows,
        admin_email=current_config.admin_email,
        admin_username=current_config.admin_username,
    )


@router.post("/admin/users/{user_id}/disable", response_model=None)
async def disable_user(
    request: Request,
    user_id: int,
    admin: accounts.Account = Depends(accounts.require_admin),
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    if user_id == admin.id:
        return _error(request, "You cannot disable your own account.")
    db = request.app.state.db
    cursor = await db.execute(
        "UPDATE users SET disabled_at = ?, session_version = session_version + 1 WHERE id = ?",
        (accounts.iso_now(), user_id),
    )
    await db.commit()
    if cursor.rowcount == 0:
        return _error(request, "User not found.", status=HTTP_404_NOT_FOUND)
    await _audit(request, admin, "admin_user_disabled", f"user:{user_id}")
    return _redirect("/admin/users")


@router.post("/admin/users/{user_id}/enable", response_model=None)
async def enable_user(
    request: Request,
    user_id: int,
    admin: accounts.Account = Depends(accounts.require_admin),
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    cursor = await db.execute("UPDATE users SET disabled_at = NULL WHERE id = ?", (user_id,))
    await db.commit()
    if cursor.rowcount == 0:
        return _error(request, "User not found.", status=HTTP_404_NOT_FOUND)
    await _audit(request, admin, "admin_user_enabled", f"user:{user_id}")
    return _redirect("/admin/users")


@router.post("/admin/users/{user_id}/role", response_model=None)
async def set_role(
    request: Request,
    user_id: int,
    admin: accounts.Account = Depends(accounts.require_admin),
    role: Annotated[str, Form()] = "user",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    if role not in ("admin", "user") or (user_id == admin.id and role != "admin"):
        return _error(request, "That role change is not allowed.")
    db = request.app.state.db
    target = await _user_row(request, user_id)
    if target is None:
        return _error(request, "User not found.", status=HTTP_404_NOT_FOUND)
    if target["role"] == "admin" and role == "user" and await _admin_count(request) <= 1:
        return _error(request, "The last admin cannot be demoted.")
    await db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    await db.commit()
    await _audit(request, admin, "admin_user_role", f"user:{user_id} role:{role}")
    return _redirect("/admin/users")


@router.post("/admin/users/{user_id}/email", response_model=None)
async def set_email(
    request: Request,
    user_id: int,
    admin: accounts.Account = Depends(accounts.require_admin),
    email: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    try:
        normalized = accounts.normalize_email(email)
    except ValueError:
        return _error(request, "That email address is not valid.")
    db = request.app.state.db
    try:
        cursor = await db.execute("UPDATE users SET email = ? WHERE id = ?", (normalized, user_id))
        await db.commit()
    except sqlite3.IntegrityError:
        return _error(request, "That email address is already in use.")
    if cursor.rowcount == 0:
        return _error(request, "User not found.", status=HTTP_404_NOT_FOUND)
    await _audit(request, admin, "admin_user_email", f"user:{user_id}")
    return _redirect("/admin/users")


@router.post("/admin/users/{user_id}/send-link", response_model=None)
async def send_link(
    request: Request,
    user_id: int,
    admin: accounts.Account = Depends(accounts.require_admin),
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    row = await _user_row(request, user_id)
    if row is None or row["email"] is None:
        return _error(request, "That user has no email address.", status=HTTP_404_NOT_FOUND)
    token = await accounts.create_login_token(
        db, str(row["email"]), ip=accounts.client_ip(request), secret=request.app.state.secret_key
    )
    link = (
        mail.build_magic_link(current_config.public_base_url, token)
        if current_config.public_base_url
        else f"/auth/verify?token={quote(token)}"
    )
    await mail.get_mailer(current_config).send(
        str(row["email"]),
        "Sign in to YouRSS+",
        f"An administrator created this link for you. It is valid for 15 minutes:\n\n{link}",
    )
    await accounts.audit(
        db,
        user_id=admin.id,
        action="admin_login_link_sent",
        detail=f"user:{user_id}",
        ip_hash=accounts.hash_ip(request.app.state.secret_key, accounts.client_ip(request)),
    )
    return _redirect("/admin/users")


@router.post("/admin/users/{user_id}/delete", response_model=None)
async def delete_user(
    request: Request,
    user_id: int,
    admin: accounts.Account = Depends(accounts.require_admin),
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    if user_id == admin.id:
        return _error(request, "You cannot delete your own account.")
    target = await _user_row(request, user_id)
    if target is None:
        return _error(request, "User not found.", status=HTTP_404_NOT_FOUND)
    if target["role"] == "admin" and await _admin_count(request) <= 1:
        return _error(request, "The last admin cannot be deleted.")
    db = request.app.state.db
    await db.execute("DELETE FROM login_tokens WHERE email = (SELECT email FROM users WHERE id = ?)", (user_id,))
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()
    await _audit(request, admin, "admin_user_deleted", f"user:{user_id}")
    return _redirect("/admin/users")


@router.get("/admin/status", response_class=HTMLResponse)
async def status_page(request: Request, admin: accounts.Account = Depends(accounts.require_admin)) -> HTMLResponse:
    db = request.app.state.db
    counts = await db.fetchall("SELECT state, COUNT(*) AS count FROM warmup_status GROUP BY state ORDER BY state")
    recent = await db.fetchall(
        "SELECT channel, state, last_ok_at, last_error, checked_at FROM warmup_status "
        "ORDER BY checked_at DESC LIMIT 100"
    )
    return template_page(
        request,
        "pages/admin-status.jinja-html",
        title="Status",
        admin=admin,
        counts=counts,
        recent=recent,
    )
