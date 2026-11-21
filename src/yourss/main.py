import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.datastructures import MutableHeaders
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __name__ as app_name
from . import __version__ as app_version
from . import accounts
from .db import connect
from .routers import account, admin, api, auth, htmx, ops, web
from .routers.jinja import template_page
from .security import LoginRequired, UnknownUser
from .settings import current_config, database_path, iso_duration, static_folder
from .subscriptions import import_users_file
from .warmup import warm_forever, warm_interval
from .watched import get_watched
from .youtube import shared_client

PRIVATE_ALLOWED_PREFIXES = (
    "/static/",
    "/login",
    "/auth/",
    "/logout",
    "/healthz",
    "/robots.txt",
    "/favicon.ico",
    "/apple-touch-icon",
)
PRIVATE_ALLOWED_EXACT = ("/api/version",)


class AccountMiddleware:
    """Put the signed-in account on the request state, gate private mode and add noindex headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > 1_048_576:
            await Response(status_code=413)(scope, receive, send)
            return
        request.state.user = await accounts.current_user(request)
        db = getattr(request.app.state, "db", None)
        if request.state.user is not None and db is not None:
            request.state.watched = await get_watched(db, request.state.user.id)
        path = scope.get("path", "")
        if current_config.private and request.state.user is None:
            allowed = path in PRIVATE_ALLOWED_EXACT or path.startswith(PRIVATE_ALLOWED_PREFIXES)
            if not allowed:
                if path.startswith("/api") or request.headers.get("hx-request") == "true":
                    response = Response(
                        status_code=401,
                        headers={"HX-Refresh": "true"} if request.headers.get("hx-request") == "true" else {},
                    )
                else:
                    response = RedirectResponse(f"/login?next={quote(path)}", status_code=303)
                await response(scope, receive, send)
                return
        noindex = (
            current_config.noindex
            or current_config.private
            or path.startswith(("/login", "/auth/", "/account", "/admin", "/u/"))
        )
        if noindex or request.state.user is not None:

            async def send_private_headers(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    if noindex:
                        headers["X-Robots-Tag"] = "noindex, nofollow"
                    if request.state.user is not None:
                        # WHY: a shared cache must never hand one account's page or JSON to another.
                        headers["Cache-Control"] = "no-store"
                        headers["Vary"] = "Cookie"
                await send(message)

            await self.app(scope, receive, send_private_headers)
            return
        await self.app(scope, receive, send)


class CachedStaticFiles(StaticFiles):
    """Static files with a browser cache policy (the default one sends none)."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        out = await super().get_response(path, scope)
        # A versioned URL (?v=<mtime>, see static_url) never changes: cache it for good
        versioned = b"v=" in scope.get("query_string", b"")
        out.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "public, max-age=3600"
        return out


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The SQLite connection, the shared Youtube client and the optional warm-up task live with the app."""
    async with shared_client():
        db = await connect(database_path(current_config))
        task = None
        try:
            await db.migrate()
            if current_config.smtp_host and not current_config.public_base_url:
                raise RuntimeError("YOURSS_PUBLIC_BASE_URL is required when YOURSS_SMTP_HOST is set")
            if bool(current_config.turnstile_site_key) != bool(current_config.turnstile_secret_key):
                raise RuntimeError("YOURSS_TURNSTILE_SITE_KEY and YOURSS_TURNSTILE_SECRET_KEY must be set together")
            if current_config.registration_enabled and current_config.admin_email is None:
                logger.warning(
                    "YOURSS_REGISTRATION_ENABLED with no YOURSS_ADMIN_EMAIL: the first verified user becomes admin"
                )
            await import_users_file(db, current_config)
            await accounts.bootstrap_admin(db, current_config)
            app.state.db = db
            app.state.secret_key = await db.secret_key(current_config.secret_key)
            if current_config.warmup_enabled and not current_config.cache_enabled:
                logger.warning("YOURSS_WARMUP_ENABLED is set but the cache is disabled: nothing to warm up")
            elif current_config.warmup_enabled:
                interval = warm_interval(current_config.cache_rss_ttl)
                logger.info("Warm-up of every page every {}", iso_duration(interval))
                task = asyncio.create_task(warm_forever(interval, db))
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await db.close()


async def unknown_user(request: Request, error: UnknownUser) -> Response:
    """An unknown user is a page, like an unknown folder or page, not a JSON detail."""
    return template_page(
        request,
        "pages/error.jinja-html",
        status_code=404,
        title="This user does not exist",
        message="No user of this instance has this address.",
        home_link=True,
    )


async def login_required(request: Request, error: LoginRequired) -> Response:
    """The login form; htmx reloads the page to show it (docs/specs/login.md §6)."""
    if request.headers.get("hx-request") == "true":
        return Response(status_code=401, headers={"HX-Refresh": "true"})
    return web.login_page(request, error.user)


def create_app(*, api_docs: bool) -> FastAPI:
    """The application, with or without the generated API documentation."""
    out = FastAPI(
        title=app_name,
        version=app_version,
        lifespan=lifespan,
        docs_url="/docs" if api_docs else None,
        redoc_url="/redoc" if api_docs else None,
        openapi_url="/openapi.json" if api_docs else None,
    )
    out.add_exception_handler(UnknownUser, unknown_user)  # type: ignore[arg-type]
    out.add_exception_handler(LoginRequired, login_required)  # type: ignore[arg-type]

    out.add_middleware(AccountMiddleware)
    # A folder of videos is a few hundred kB of HTML, twenty times less once compressed
    out.add_middleware(GZipMiddleware, minimum_size=1000)
    out.include_router(ops.router)
    out.include_router(auth.router)
    out.include_router(account.router)
    out.include_router(admin.router)
    out.include_router(web.router)
    out.include_router(htmx.router)
    out.include_router(api.router)
    out.mount("/static", CachedStaticFiles(directory=static_folder), name="static")
    out.include_router(ops.fallback_router)
    return out


app = create_app(api_docs=current_config.api_docs_enabled)
