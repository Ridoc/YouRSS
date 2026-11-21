"""Health, robots, favicon and the final 404."""

from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.responses import HTMLResponse
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..settings import current_config, static_folder
from ..youtube.cache import cache_folder
from .jinja import template_page

router = APIRouter()
fallback_router = APIRouter()


async def _cache_is_writable() -> bool:
    folder = cache_folder()
    if folder is None:
        return True
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix="health-", delete=True):
            pass
    except OSError:
        return False
    return os.access(folder, os.W_OK)


@router.get("/healthz", response_model=None)
async def healthz(request: Request) -> JSONResponse:
    try:
        await request.app.state.db.fetchone("SELECT 1")
        if not await _cache_is_writable():
            raise RuntimeError("cache is not writable")
    except Exception:
        return JSONResponse({"status": "error"}, status_code=HTTP_503_SERVICE_UNAVAILABLE)
    return JSONResponse({"status": "ok"})


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> PlainTextResponse:
    if current_config.noindex or current_config.private:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    return PlainTextResponse("User-agent: *\nAllow: /\n")


@router.get("/favicon.ico", response_class=FileResponse)
async def favicon() -> FileResponse:
    return FileResponse(static_folder / "icons" / "favicon-32.png", headers={"Cache-Control": "public, max-age=3600"})


@fallback_router.get("/{path:path}", response_class=HTMLResponse, response_model=None)
async def not_found(request: Request, path: str) -> HTMLResponse:
    return template_page(
        request,
        "pages/error.jinja-html",
        status_code=404,
        title="This page does not exist",
        message="No page of this instance has this address.",
        home_link=True,
    )
