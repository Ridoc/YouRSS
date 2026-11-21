"""Self-service account, pages and subscriptions."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER, HTTP_404_NOT_FOUND

from .. import accounts
from ..settings import current_config
from ..subscriptions import (
    SubscriptionError,
    add_subscription,
    create_page,
    delete_page,
    delete_subscription,
    get_page,
    list_pages,
    list_subscriptions,
    rename_page,
    reorder_subscriptions,
    set_publish,
    set_warmup,
)
from ..youtube import YoutubeApi
from .jinja import template_page

router = APIRouter()
VIDEO_PREFIXES = ("https://www.youtube.com/watch?v=", "https://youtube.com/watch?v=", "https://youtu.be/")


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=HTTP_303_SEE_OTHER)


def _error(request: Request, message: str, *, status: int = 400) -> HTMLResponse:
    return template_page(
        request,
        "pages/error.jinja-html",
        status_code=status,
        title="Account",
        message=message,
        home_link=True,
    )


async def _resolve_ref(ref: str) -> tuple[str, str]:
    ref = ref.strip()
    if ref.startswith("PL"):
        return "playlist", ref
    if ref.startswith("UC") and len(ref) == 24:
        return "channel", ref
    if ref.startswith("@"):
        channel = await YoutubeApi().get_channel(ref)
        return "channel", channel.channel_id
    if ref.startswith(VIDEO_PREFIXES):
        video_id = ref.rsplit("v=", 1)[-1] if "watch?v=" in ref else ref.rsplit("/", 1)[-1]
        channel_id = await YoutubeApi().get_video_channel(video_id[:11])
        return "channel", channel_id
    raise SubscriptionError("Use a channel id (UC…), a handle (@name), a playlist id (PL…) or a video URL")


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, user: accounts.Account = Depends(accounts.require_user)) -> HTMLResponse:
    db = request.app.state.db
    pages = await list_pages(db, user.id)
    watched = await db.fetchone("SELECT COUNT(*) FROM watched WHERE user_id = ?", (user.id,))
    return template_page(
        request,
        "pages/account.jinja-html",
        title="Account",
        account=user,
        pages=pages,
        watched_count=int(watched[0]) if watched is not None else 0,
        max_pages=current_config.max_pages_per_user,
    )


@router.post("/account", response_model=None)
async def update_account(
    request: Request,
    user: accounts.Account = Depends(accounts.require_user),
    username: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    username = username.strip().lower()
    if username and username != user.username:
        if username in accounts.RESERVED_USERNAMES or not accounts.USERNAME_RE.match(username):
            return _error(request, "That username is not available.")
        try:
            await db.execute(
                "UPDATE users SET username = ? WHERE id = ?", (username, user.id)
            )
            await db.commit()
        except sqlite3.IntegrityError:
            return _error(request, "That username is already taken.")
    await db.execute(
        "UPDATE users SET display_name = ? WHERE id = ?", (display_name.strip()[:80], user.id)
    )
    await db.commit()
    return _redirect("/account")


@router.post("/account/logout-all")
async def logout_all(
    request: Request,
    user: accounts.Account = Depends(accounts.require_user),
    csrf_token: Annotated[str, Form()] = "",
) -> RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    await db.execute(
        "UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user.id,)
    )
    await db.commit()
    out = _redirect("/login")
    out.delete_cookie(accounts.SESSION_COOKIE, path="/")
    return out


@router.get("/account/export")
async def export_account(request: Request, user: accounts.Account = Depends(accounts.require_user)) -> JSONResponse:
    db = request.app.state.db
    pages = await list_pages(db, user.id)
    created = await db.fetchone("SELECT created_at FROM users WHERE id = ?", (user.id,))
    payload: dict[str, object] = {
        "user": {
            "username": user.username,
            "email": user.email,
            "display_name": user.display_name,
            "created_at": str(created["created_at"]) if created is not None else None,
        },
        "pages": [],
    }
    page_payload: list[dict[str, object]] = []
    for page in pages:
        subscriptions = await list_subscriptions(db, int(page["id"]))
        page_payload.append(
            {
                "name": page["name"],
                "publish": page["publish"],
                "warmup": bool(page["warmup"]),
                "subscriptions": [
                    {"ref": row["ref"], "kind": row["kind"], "canonical_id": row["canonical_id"]}
                    for row in subscriptions
                ],
            }
        )
    payload["pages"] = page_payload
    watched = await db.fetchall("SELECT video_id, watched_at FROM watched WHERE user_id = ?", (user.id,))
    payload["watched"] = {str(row["video_id"]): int(row["watched_at"]) for row in watched}
    return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="yourss-account.json"'})


@router.post("/account/delete", response_model=None)
async def delete_account(
    request: Request,
    user: accounts.Account = Depends(accounts.require_user),
    confirm: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    if confirm != "DELETE":
        return _error(request, 'Type DELETE to confirm account deletion.')
    db = request.app.state.db
    admin_count = await db.fetchone("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    if user.role == "admin" and admin_count is not None and int(admin_count[0]) <= 1:
        return _error(request, "The last admin cannot be deleted.")
    if user.email:
        await db.execute("DELETE FROM login_tokens WHERE email = ?", (user.email,))
    await db.execute("DELETE FROM users WHERE id = ?", (user.id,))
    await db.commit()
    out = _redirect("/login")
    out.delete_cookie(accounts.SESSION_COOKIE, path="/")
    return out


@router.post("/account/pages", response_model=None)
async def create_page_route(
    request: Request,
    user: accounts.Account = Depends(accounts.require_user),
    name: Annotated[str, Form()] = "",
    publish: Annotated[str, Form()] = "",
    warmup: Annotated[str, Form()] = "on",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    try:
        page = await create_page(
            db,
            current_config,
            user.id,
            name,
            publish=publish.strip() or None,
            warmup=warmup == "on",
        )
    except SubscriptionError as error:
        return _error(request, str(error))
    return _redirect(f"/account/pages/{page['name']}")


@router.get("/account/pages/{name}", response_class=HTMLResponse)
async def page_editor(
    request: Request,
    name: str,
    user: accounts.Account = Depends(accounts.require_user),
) -> HTMLResponse:
    db = request.app.state.db
    page = await get_page(db, user.id, name)
    if page is None:
        raise HTTPException(HTTP_404_NOT_FOUND, "Page not found")
    subscriptions = await list_subscriptions(db, int(page["id"]))
    return template_page(
        request,
        "pages/account-page.jinja-html",
        title=f"Edit {page['name']}",
        account=user,
        page=page,
        subscriptions=subscriptions,
        max_subscriptions=current_config.max_page_subscriptions,
    )


@router.post("/account/pages/{name}", response_model=None)
async def update_page(
    request: Request,
    name: str,
    user: accounts.Account = Depends(accounts.require_user),
    new_name: Annotated[str, Form()] = "",
    publish: Annotated[str, Form()] = "",
    warmup: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    page = await get_page(db, user.id, name)
    if page is None:
        raise HTTPException(HTTP_404_NOT_FOUND, "Page not found")
    target = new_name.strip() or name
    try:
        if target != name:
            await rename_page(db, user.id, name, target)
        await set_publish(db, user.id, target, publish.strip() or None)
        await set_warmup(db, user.id, target, warmup == "on")
    except SubscriptionError as error:
        return _error(request, str(error))
    return _redirect(f"/account/pages/{target}")


@router.post("/account/pages/{name}/delete", response_model=None)
async def delete_page_route(
    request: Request,
    name: str,
    user: accounts.Account = Depends(accounts.require_user),
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    try:
        await delete_page(db, user.id, name)
    except SubscriptionError as error:
        return _error(request, str(error))
    return _redirect("/account")


@router.post("/account/pages/{name}/subscriptions", response_model=None)
async def add_subscription_route(
    request: Request,
    name: str,
    user: accounts.Account = Depends(accounts.require_user),
    ref: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    page = await get_page(db, user.id, name)
    if page is None:
        raise HTTPException(HTTP_404_NOT_FOUND, "Page not found")
    try:
        kind, canonical = await _resolve_ref(ref)
        await add_subscription(db, current_config, int(page["id"]), ref, canonical, kind=kind)
    except (SubscriptionError, ValueError) as error:
        return _error(request, str(error))
    except Exception:
        return _error(request, "That channel or playlist could not be resolved.")
    return _redirect(f"/account/pages/{name}")


@router.post("/account/pages/{name}/subscriptions/{subscription_id}/delete", response_model=None)
async def delete_subscription_route(
    request: Request,
    name: str,
    subscription_id: int,
    user: accounts.Account = Depends(accounts.require_user),
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    page = await get_page(db, user.id, name)
    if page is None:
        raise HTTPException(HTTP_404_NOT_FOUND, "Page not found")
    try:
        await delete_subscription(db, int(page["id"]), subscription_id)
    except SubscriptionError as error:
        return _error(request, str(error))
    return _redirect(f"/account/pages/{name}")


@router.post("/account/pages/{name}/subscriptions/reorder", response_model=None)
async def reorder_subscriptions_route(
    request: Request,
    name: str,
    user: accounts.Account = Depends(accounts.require_user),
    ids: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    accounts.validate_csrf(request, csrf_token)
    db = request.app.state.db
    page = await get_page(db, user.id, name)
    if page is None:
        raise HTTPException(HTTP_404_NOT_FOUND, "Page not found")
    try:
        order = [int(value) for value in ids.split(",") if value.strip()]
        await reorder_subscriptions(db, int(page["id"]), order)
    except (SubscriptionError, ValueError) as error:
        return _error(request, str(error))
    return _redirect(f"/account/pages/{name}")


