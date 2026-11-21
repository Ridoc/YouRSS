"""Passwordless magic-link login, verification and logout."""

from __future__ import annotations

from time import time
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER, HTTP_429_TOO_MANY_REQUESTS

from .. import accounts, captcha, mail, ratelimit
from ..settings import current_config
from .jinja import template_page

router = APIRouter()


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/account"


def _secure(request: Request) -> bool:
    base = current_config.public_base_url or ""
    return request.url.scheme == "https" or base.startswith("https://")


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        HTTP_429_TOO_MANY_REQUESTS,
        "Too many requests. Wait a moment and try again.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


@router.get("/login", response_class=HTMLResponse, response_model=None)
async def login(
    request: Request,
    next: str | None = Query(None),
    sent: int = 0,
) -> HTMLResponse | RedirectResponse:
    user = await accounts.current_user(request)
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=HTTP_303_SEE_OTHER)
    return template_page(
        request,
        "pages/login.jinja-html",
        title="Sign in",
        next=_safe_next(next),
        sent=bool(sent),

        turnstile_site_key=current_config.turnstile_site_key,
        legacy_login=current_config.users_file is not None,
    )


@router.post("/auth/request")
async def request_link(
    request: Request,
    background: BackgroundTasks,
    email: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
    turnstile: Annotated[str | None, Form(alias="cf-turnstile-response")] = None,
) -> RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    ip = accounts.client_ip(request)
    if not ratelimit.login_ip_limiter.allow(ip or "") or not ratelimit.auth_general_limiter.allow(ip or ""):
        raise _too_many(max(ratelimit.login_ip_limiter.retry_after(ip or ""), 1))
    try:
        normalized = accounts.normalize_email(email)
    except ValueError:
        return RedirectResponse(f"/login?sent=1&next={quote(_safe_next(next))}", status_code=HTTP_303_SEE_OTHER)
    if not ratelimit.login_email_limiter.allow(normalized):
        raise _too_many(ratelimit.login_email_limiter.retry_after(normalized))
    turnstile_secret = (
        current_config.turnstile_secret_key.get_secret_value()
        if current_config.turnstile_secret_key is not None
        else None
    )
    if not await captcha.verify_turnstile(turnstile_secret, turnstile, ip):
        raise HTTPException(403, "Verification failed. Try again.")

    db = request.app.state.db
    row = await db.fetchone("SELECT 1 FROM users WHERE email = ? COLLATE NOCASE", (normalized,))
    if row is not None or current_config.registration_enabled:
        token = await accounts.create_login_token(db, normalized, ip=ip, secret=request.app.state.secret_key)
        # SMTP deployments must set PUBLIC_BASE_URL at startup; the dev log mailer may use a relative link.
        link = (
            mail.build_magic_link(current_config.public_base_url, token)
            if current_config.public_base_url
            else f"/auth/verify?token={quote(token)}"
        )
        background.add_task(
            mail.get_mailer(current_config).send,
            normalized,
            "Sign in to YouRSS+",
            f"Open this link within 15 minutes to sign in:\n\n{link}\n\nIf you did not ask for it, ignore this email.",
        )
        await accounts.audit(
            db,
            user_id=None,
            action="login_link_sent",
            detail="magic-link",
            ip_hash=accounts.hash_ip(request.app.state.secret_key, ip),
        )
    # Same answer for known, unknown and malformed addresses.
    return RedirectResponse(f"/login?sent=1&next={quote(_safe_next(next))}", status_code=HTTP_303_SEE_OTHER)


@router.get("/auth/verify", response_class=HTMLResponse)
async def verify_page(request: Request, token: str = Query("")) -> HTMLResponse:
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT email, expires_at, used_at FROM login_tokens WHERE token_hash = ?",
        (accounts.hash_token(token),),
    )
    valid = row is not None and row["used_at"] is None and int(row["expires_at"]) > int(time())
    response = template_page(
        request,
        "pages/auth-verify.jinja-html",
        title="Confirm sign-in",
        token=token if valid else None,
        email=str(row["email"]) if valid and row is not None else None,
        valid=valid,
        expired=row is not None and not valid,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/auth/verify", response_model=None)
async def verify(
    request: Request,
    token: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    ip = accounts.client_ip(request)
    if not ratelimit.verify_ip_limiter.allow(ip or "") or not ratelimit.auth_general_limiter.allow(ip or ""):
        raise _too_many(max(ratelimit.verify_ip_limiter.retry_after(ip or ""), 1))
    db = request.app.state.db
    email = await accounts.consume_login_token(db, token)
    if email is None:
        return template_page(
            request,
            "pages/auth-verify.jinja-html",
            status_code=400,
            title="Confirm sign-in",
            token=None,
            valid=False,
            expired=True,
        )
    user = await accounts.get_or_create_user(db, email, current_config)
    if user is None:
        return template_page(
            request,
            "pages/auth-verify.jinja-html",
            status_code=403,
            title="Registration closed",
            token=None,
            valid=False,
            registration_closed=True,
        )
    user = await accounts.promote_first_verified_user(db, user, current_config)
    if user.disabled_at is not None:
        return template_page(
            request,
            "pages/auth-verify.jinja-html",
            status_code=403,
            title="Account disabled",
            token=None,
            valid=False,
            disabled=True,
        )
    user = await accounts.clear_legacy_credentials(db, user)
    response = RedirectResponse(_safe_next(next), status_code=HTTP_303_SEE_OTHER)
    accounts.set_session_cookie(
        response, request.app.state.secret_key, user, current_config.session_ttl, secure=_secure(request)
    )
    await accounts.audit(
        db,
        user_id=user.id,
        action="login",
        detail="magic-link",
        ip_hash=accounts.hash_ip(request.app.state.secret_key, ip),
    )
    return response


@router.post("/logout")
async def logout(request: Request, csrf_token: Annotated[str, Form()] = "") -> RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    user = await accounts.current_user(request)
    response = RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)
    accounts.clear_session_cookie(response)
    if user is not None:
        await request.app.state.db.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user.id,)
        )
        await request.app.state.db.commit()
        await accounts.audit(
            request.app.state.db,
            user_id=user.id,
            action="logout",
            ip_hash=accounts.hash_ip(request.app.state.secret_key, accounts.client_ip(request)),
        )
    return response
