"""Health endpoint (C5.I2)."""

from typing import TYPE_CHECKING

from httpx import AsyncClient
from pytest import MonkeyPatch

from yourss.routers import ops

if TYPE_CHECKING:
    from yourss.db import Database


async def test_healthz_ok(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_reports_a_database_failure(
    client: AsyncClient, db: "Database", monkeypatch: MonkeyPatch
) -> None:
    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database is gone")

    monkeypatch.setattr(db, "fetchone", fail)
    response = await client.get("/healthz")
    assert response.status_code == 503


async def test_healthz_reports_an_unwritable_cache(client: AsyncClient, monkeypatch: MonkeyPatch) -> None:
    async def unwritable() -> bool:
        return False

    monkeypatch.setattr(ops, "_cache_is_writable", unwritable)
    response = await client.get("/healthz")
    assert response.status_code == 503
