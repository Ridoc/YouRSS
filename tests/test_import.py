import re
from collections.abc import AsyncIterator
from pathlib import Path

from httpx import AsyncClient
from pydantic_yaml import parse_yaml_raw_as
from pytest import MonkeyPatch, fixture, mark, raises
from pytest_asyncio import fixture as async_fixture

from yourss import accounts
from yourss.db import Database
from yourss.schema import AppSettings, UsersConfig
from yourss.settings import current_config
from yourss.subscriptions import import_users_file

DATA = Path(__file__).parent / "data" / "users-zeno.yaml"
# The argon2 hash of "foo" published by samples/users.yaml: a dummy, never a secret.
ARGON2_HASH = "$argon2i$v=19$m=4096,t=3,p=1$c29tZXNhbHQ$y+awVl4ev1h6eG8IbOrPAAjANRIbraM51FL50Pcf/B8"
PAGES = 12
SUBSCRIPTIONS = 149
HANDLES = 73

REPLACE_ME_USERS = """
users:
  - name: zeno
    password: { method: clear, value: REPLACE_ME }
    pages:
      - name: Page01
        channels: ["@creator01", "REPLACE_ME"]
"""

INVALID_USERS = """
users: 42
"""


@fixture(autouse=True)
def no_startup_import(monkeypatch: MonkeyPatch) -> None:
    """The application lifespan must not import the developer users file into the shared test database."""
    monkeypatch.setattr(current_config, "users_file", None)


async def clean_import_state(db: Database) -> None:
    """The suite shares one database: an import test starts with no users and no marker."""
    await db.execute("DELETE FROM users")
    await db.execute("DELETE FROM settings WHERE key = ?", ("users_imported_at",))
    await db.commit()


@async_fixture(autouse=True, loop_scope="function")
async def import_isolation(no_startup_import: None, db: Database) -> AsyncIterator[None]:
    await clean_import_state(db)
    yield
    await clean_import_state(db)


def settings_for(path: Path | None, *, pages: int = 0, subs: int = 0) -> AppSettings:
    """The import reads the file and the safety floors; the database is passed beside it."""
    return AppSettings(users_file=path, import_expect_pages=pages, import_expect_subs=subs)


async def assert_nothing_imported(db: Database) -> None:
    assert await db.get_setting("users_imported_at") is None
    assert await db.fetchone("SELECT id FROM users WHERE username = ?", ("zeno",)) is None


async def test_imports_the_users_pages_and_subscriptions(db: Database) -> None:
    before = DATA.read_bytes()
    config = parse_yaml_raw_as(UsersConfig, before)
    await import_users_file(db, settings_for(DATA))

    # The source file is only read: a read-only mount must keep working
    assert DATA.read_bytes() == before
    assert len(config.pages) == PAGES
    assert sum(len(page.channels) for page in config.pages) == SUBSCRIPTIONS
    published = [page.publish for page in config.pages if page.publish is not None]
    assert len(set(published)) == len(published)
    assert sum(not page.warmup for page in config.pages) == 1
    assert len({ref for page in config.pages for ref in page.channels if ref.startswith("@")}) == HANDLES

    user = await db.fetchone(
        "SELECT id, email, role, legacy_password_hash, legacy_password_method FROM users WHERE username = ?",
        ("zeno",),
    )
    assert user is not None
    assert user["email"] is None
    assert user["role"] == "user"
    assert user["legacy_password_hash"] == ARGON2_HASH
    assert user["legacy_password_method"] == "argon2"  # noqa: S105 - method name, not a password

    pages = await db.fetchall(
        "SELECT id, name, publish, warmup, position FROM pages WHERE user_id = ? ORDER BY position",
        (user["id"],),
    )
    assert len(pages) == PAGES
    assert [page["name"] for page in pages] == [page.name for page in config.pages]
    assert [page["publish"] for page in pages] == [page.publish for page in config.pages]
    assert [bool(page["warmup"]) for page in pages] == [page.warmup for page in config.pages]
    positions = [int(page["position"]) for page in pages]
    assert positions == sorted(positions) and len(set(positions)) == PAGES

    for page, expected in zip(pages, config.pages, strict=True):
        subscriptions = await db.fetchall(
            "SELECT ref, kind, canonical_id, position FROM subscriptions WHERE page_id = ? ORDER BY position",
            (page["id"],),
        )
        assert [row["ref"] for row in subscriptions] == expected.channels
        # An unresolved handle is kept as its own canonical id
        assert all(row["canonical_id"] == row["ref"] for row in subscriptions)
        kinds = ["playlist" if ref.startswith("PL") else "channel" for ref in expected.channels]
        assert [row["kind"] for row in subscriptions] == kinds
        subscription_positions = [int(row["position"]) for row in subscriptions]
        assert subscription_positions == sorted(subscription_positions)
        assert len(set(subscription_positions)) == len(expected.channels)

    total = await db.fetchone(
        "SELECT COUNT(*) FROM subscriptions s JOIN pages p ON p.id = s.page_id WHERE p.user_id = ?",
        (user["id"],),
    )
    assert total is not None and int(total[0]) == SUBSCRIPTIONS
    handles = await db.fetchall(
        "SELECT DISTINCT s.ref FROM subscriptions s"
        " JOIN pages p ON p.id = s.page_id"
        " WHERE p.user_id = ? AND s.ref LIKE '@%'",
        (user["id"],),
    )
    assert len(handles) == HANDLES
    marker = await db.get_setting("users_imported_at")
    assert marker is not None and marker.strip()


async def test_a_second_import_is_a_no_op(db: Database) -> None:
    settings = settings_for(DATA)
    await import_users_file(db, settings)
    first = await db.get_setting("users_imported_at")
    assert first is not None

    await import_users_file(db, settings)

    assert await db.get_setting("users_imported_at") == first
    user = await db.fetchone("SELECT id FROM users WHERE username = ?", ("zeno",))
    assert user is not None
    pages = await db.fetchone("SELECT COUNT(*) FROM pages WHERE user_id = ?", (user["id"],))
    subscriptions = await db.fetchone(
        "SELECT COUNT(*) FROM subscriptions s JOIN pages p ON p.id = s.page_id WHERE p.user_id = ?",
        (user["id"],),
    )
    duplicates = await db.fetchone("SELECT COUNT(*) FROM users WHERE username = ?", ("zeno",))
    assert pages is not None and int(pages[0]) == PAGES
    assert subscriptions is not None and int(subscriptions[0]) == SUBSCRIPTIONS
    assert duplicates is not None and int(duplicates[0]) == 1


async def test_unset_users_file_leaves_the_marker_unset(db: Database) -> None:
    await import_users_file(db, settings_for(None))
    await assert_nothing_imported(db)


@mark.parametrize(("pages", "subs"), [(13, 0), (0, 150), (13, 150)])
async def test_expect_count_floor_fails_before_commit(db: Database, pages: int, subs: int) -> None:
    with raises(RuntimeError):
        await import_users_file(db, settings_for(DATA, pages=pages, subs=subs))
    await assert_nothing_imported(db)


async def test_replace_me_content_is_rejected(db: Database, tmp_path: Path) -> None:
    path = tmp_path / "users.yaml"
    path.write_text(REPLACE_ME_USERS, encoding="utf-8")
    with raises(RuntimeError):
        await import_users_file(db, settings_for(path))
    await assert_nothing_imported(db)


async def test_invalid_users_file_is_rejected(db: Database, tmp_path: Path) -> None:
    path = tmp_path / "users.yaml"
    path.write_text(INVALID_USERS, encoding="utf-8")
    with raises(RuntimeError):
        await import_users_file(db, settings_for(path))
    await assert_nothing_imported(db)


async def test_missing_users_file_is_rejected(db: Database, tmp_path: Path) -> None:
    with raises(RuntimeError):
        await import_users_file(db, settings_for(tmp_path / "absent.yaml"))
    await assert_nothing_imported(db)


async def test_imported_user_claims_the_legacy_account(client: AsyncClient, db: Database) -> None:
    await import_users_file(db, settings_for(DATA))
    await accounts.bootstrap_admin(db, AppSettings(admin_email="zeno@example.com", admin_username="zeno"))
    assert (await client.get("/u/zeno")).status_code == 401
    token = await accounts.create_login_token(db, "zeno@example.com")
    page = await client.get("/auth/verify", params={"token": token})
    assert page.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    response = await client.post("/auth/verify", data={"token": token, "csrf_token": match.group(1)})
    assert response.status_code == 303
    assert (await client.get("/u/zeno")).status_code == 200
    row = await db.fetchone("SELECT legacy_password_hash, email FROM users WHERE username = 'zeno'")
    assert row is not None and row["legacy_password_hash"] is None and row["email"] == "zeno@example.com"


async def test_bootstrap_admin_creates_the_admin_from_email(db: Database) -> None:
    config = AppSettings(admin_email="root@example.com", admin_username="owner")
    await accounts.bootstrap_admin(db, config)
    row = await db.fetchone("SELECT email, username, role FROM users WHERE email = ?", ("root@example.com",))
    assert row is not None and row["username"] == "owner" and row["role"] == "admin"


async def test_bootstrap_admin_adopts_an_imported_username(db: Database) -> None:
    await import_users_file(db, settings_for(DATA))
    await accounts.bootstrap_admin(db, AppSettings(admin_email="old@example.com", admin_username="zeno"))
    row = await db.fetchone("SELECT id, email, role FROM users WHERE username = 'zeno'")
    assert row is not None and row["email"] == "old@example.com" and row["role"] == "admin"
    await accounts.bootstrap_admin(db, AppSettings())
    assert (await db.fetchone("SELECT COUNT(*) FROM users"))[0] == 1  # type: ignore[index]
