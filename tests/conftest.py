import os
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import TYPE_CHECKING, Any, cast

from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pytest import Config, Item, MonkeyPatch, fixture, mark
from pytest_asyncio import fixture as async_fixture
from starlette.requests import Request

from tests.youtube_fake import FakeYoutube, Mode

if TYPE_CHECKING:
    from yourss.db import Database

# WHY: AppSettings is built at import time; the DB path must be set before yourss.main is imported.
_DB_ROOT = Path(mkdtemp(prefix="yourss-test-db-"))
os.environ["YOURSS_DB_PATH"] = str(_DB_ROOT / "yourss.db")
# CI has no local .env: make the legacy fixture users file explicit for the upstream web/warm-up tests.
os.environ.setdefault("YOURSS_USERS_FILE", str(Path(__file__).resolve().parents[1] / "samples" / "users.yaml"))

from yourss.main import app  # noqa: E402
from yourss.settings import current_config  # noqa: E402
from yourss.youtube import YoutubeApi  # noqa: E402

# How the tests reach Youtube (docs/specs/youtube-fake.md §2): recorded answers unless told otherwise
YOUTUBE_MODE = Mode(os.environ.get("YOURSS_TEST_YOUTUBE", Mode.REPLAY))


def pytest_configure(config: Config) -> None:
    config.addinivalue_line("markers", "live: needs the real Youtube, skipped unless YOURSS_TEST_YOUTUBE=live")


def pytest_collection_modifyitems(items: list[Item]) -> None:
    if YOUTUBE_MODE != Mode.LIVE:
        for item in items:
            if item.get_closest_marker("live"):
                item.add_marker(mark.skip(reason="needs the real Youtube (YOURSS_TEST_YOUTUBE=live)"))


@async_fixture(loop_scope="function")
async def client() -> AsyncIterator[AsyncClient]:
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="https://test", follow_redirects=False) as client,
    ):
        yield client


def _app(client: AsyncClient) -> FastAPI:
    return cast(FastAPI, cast(ASGITransport, client._transport).app)


@fixture
def fastapi_app(client: AsyncClient) -> FastAPI:
    """The application the client talks to, its lifespan started (so its state is populated)."""
    return _app(client)


@fixture
def secret_key(client: AsyncClient) -> str:
    """The instance signing key, from YOURSS_SECRET_KEY or generated into the settings table."""
    return cast(str, _app(client).state.secret_key)


@async_fixture(loop_scope="function")
async def db(client: AsyncClient) -> "Database":
    return cast("Database", _app(client).state.db)


@async_fixture(loop_scope="function")
async def seed_user(db: "Database") -> Callable[..., Awaitable[int]]:
    """Insert a user row directly; the tests own their rows, the app reads the same database."""

    async def seed(
        username: str,
        *,
        email: str | None = None,
        role: str = "user",
        session_version: int = 1,
        disabled: bool = False,
        legacy_password: str | None = None,
        legacy_method: str | None = None,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO users (email, username, role, created_at, disabled_at, session_version,"
            " legacy_password_hash, legacy_password_method) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (email, username, role, now, now if disabled else None, session_version, legacy_password, legacy_method),
        )
        await db.commit()
        row = await db.fetchone("SELECT id FROM users WHERE username = ?", (username,))
        assert row is not None
        return int(row["id"])

    return seed


@fixture(autouse=True)
def reset_rate_limiters() -> Iterator[None]:
    """The limiters are in-process state: a test never inherits the windows of the previous one."""
    try:
        from yourss import ratelimit
    except ImportError:  # RED phase: the module lands with the auth batch
        yield
        return
    limiters = (
        ratelimit.login_ip_limiter,
        ratelimit.login_email_limiter,
        ratelimit.verify_ip_limiter,
        ratelimit.auth_general_limiter,
    )
    for limiter in limiters:
        limiter.reset()
    yield
    for limiter in limiters:
        limiter.reset()


def request_with(app: FastAPI, *, cookie: str = "", method: str = "GET") -> Request:
    """A bare HTTP request for the dependencies that read `request.app.state` and the cookies."""
    headers = [(b"cookie", cookie.encode())] if cookie else []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "app": app,
    }
    return Request(cast(Any, scope))


@fixture(autouse=True, scope="session")
def test_database_folder() -> Iterator[Path]:
    """Remove the temporary SQLite directory once the suite is done."""
    yield _DB_ROOT
    shutil.rmtree(_DB_ROOT, ignore_errors=True)


@fixture(autouse=True, scope="session")
def test_cache_folder() -> Iterator[Path]:
    """The suite caches in its own temporary folder, never in the one of the development server."""
    folder = Path(mkdtemp(prefix="yourss-test-"))
    with MonkeyPatch.context() as patch:
        patch.setattr(current_config, "cache_enabled", True)
        patch.setattr(current_config, "cache_folder", folder)
        yield folder
    shutil.rmtree(folder, ignore_errors=True)


@fixture(autouse=True, scope="session")
def fake_youtube() -> Iterator[FakeYoutube | None]:
    """Every `YoutubeApi()` of the suite talks to the fake transport, except in live mode."""
    if YOUTUBE_MODE == Mode.LIVE:
        yield None
        return
    fake = FakeYoutube(YOUTUBE_MODE)
    original = YoutubeApi.__init__

    def with_fake(self: YoutubeApi, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("async_client_factory", lambda **options: AsyncClient(transport=fake, **options))
        original(self, *args, **kwargs)

    with MonkeyPatch.context() as patch:
        patch.setattr(YoutubeApi, "__init__", with_fake)
        yield fake
