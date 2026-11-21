from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from httpx import HTTPError
from pydantic import BaseModel, Field, StringConstraints
from starlette.status import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND, HTTP_422_UNPROCESSABLE_CONTENT

from .. import __name__ as app_name
from .. import __version__ as app_version
from .. import accounts
from .. import watched as watched_store
from ..settings import current_config
from ..youtube import (
    ScrapingError,
    YoutubeApi,
    parse_channel_reference,
    parse_playlist_reference,
    parse_video_reference,
)

router = APIRouter(prefix="/api")


@router.get("/version")
async def version() -> dict[str, str]:
    return {"name": app_name, "version": app_version}


class WatchedUpdate(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=500)
    remove: list[str] = Field(default_factory=list, max_length=500)


VideoId = Annotated[str, StringConstraints(max_length=32)]


class WatchedImport(BaseModel):
    # WHY: an unbounded map turns one authenticated POST into a million SQLite writes.
    watched: dict[VideoId, int] = Field(max_length=3000)


def _watched_csrf(request: Request, token: str | None) -> None:
    accounts.validate_csrf(request, token)


@router.get("/watched")
async def get_watched(request: Request, user: accounts.Account = Depends(accounts.require_user)) -> dict[str, int]:
    return await watched_store.get_watched(request.app.state.db, user.id)


@router.post("/watched")
async def update_watched(
    request: Request,
    payload: WatchedUpdate,
    user: accounts.Account = Depends(accounts.require_user),
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> dict[str, int]:
    _watched_csrf(request, csrf_token)
    return await watched_store.set_watched(request.app.state.db, user.id, add=payload.add, remove=payload.remove)


@router.post("/watched/import")
async def import_watched(
    request: Request,
    payload: WatchedImport,
    user: accounts.Account = Depends(accounts.require_user),
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> dict[str, int]:
    _watched_csrf(request, csrf_token)
    return await watched_store.import_watched(request.app.state.db, user.id, payload.watched)


async def _channel_name(api: YoutubeApi, text: str) -> str | None:
    # Only ids and handles taken from the text reach Youtube, never the address itself
    if (name := parse_channel_reference(text)) is not None:
        return name
    if (video_id := parse_video_reference(text)) is not None:
        return await api.get_video_channel(video_id)
    return None


@router.get("/resolve")
async def resolve(q: Annotated[str, Query(min_length=1, max_length=200)]) -> dict[str, str]:
    """Resolve what a visitor typed to a channel or a playlist (docs/specs/custom-pages.md)."""
    if not current_config.custom_pages_enabled:
        raise HTTPException(HTTP_403_FORBIDDEN, detail="Custom pages are disabled on this instance")
    api = YoutubeApi()
    try:
        if (playlist_id := parse_playlist_reference(q)) is not None:
            feed = await api.get_playlist_rss(playlist_id)
            return {"kind": "playlist", "id": playlist_id, "name": feed.title}
        name = await _channel_name(api, q)
        if name is None:
            raise HTTPException(
                HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Type a handle, a channel_id, a playlist id, or the address of a channel, video or playlist",
            )
        out = await api.get_channel(name)
    except (HTTPError, ScrapingError) as error:
        raise HTTPException(HTTP_404_NOT_FOUND, detail=f"Nothing found for {q.strip()}") from error
    return {"kind": "channel", "id": out.channel_id, "name": out.name}
