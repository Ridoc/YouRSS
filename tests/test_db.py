import sqlite3
import stat
from pathlib import Path

import pytest
from loguru import logger
from pydantic import SecretStr

from yourss.db import SCHEMA_VERSION, connect


def assert_private(path: Path) -> None:
    """Blocking stat in a helper: ASYNC240 forbids it inside the async test body."""
    assert path.exists(), f"missing {path.name}"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


TABLES = {
    "users",
    "pages",
    "subscriptions",
    "watched",
    "login_tokens",
    "settings",
    "audit_log",
    "video_durations",
    "warmup_status",
}


async def test_migrate_creates_the_schema(tmp_path: Path) -> None:
    db = await connect(tmp_path / "yourss.db")
    try:
        await db.migrate()
        version = await db.fetchone("PRAGMA user_version")
        assert version is not None and int(version[0]) == SCHEMA_VERSION
        tables = {str(row[0]) for row in await db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert TABLES.issubset(tables)

        columns = {str(row[1]) for row in await db.fetchall("PRAGMA table_info(users)")}
        assert {
            "email",
            "username",
            "display_name",
            "role",
            "created_at",
            "disabled_at",
            "session_version",
            "legacy_password_hash",
            "legacy_password_method",
            "watched_imported_at",
        } <= columns

        foreign_keys = await db.fetchall("PRAGMA foreign_key_list(pages)")
        assert any(str(row[2]) == "users" and str(row[6]) == "CASCADE" for row in foreign_keys)

        await db.execute(
            "INSERT INTO users (email, username, created_at) VALUES (?, ?, ?)",
            ("alice@example.com", "alice", "2026-01-01T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(
                "INSERT INTO users (email, username, created_at) VALUES (?, ?, ?)",
                ("alice@example.com", "other", "2026-01-01T00:00:00Z"),
            )
    finally:
        await db.close()


async def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db = await connect(tmp_path / "yourss.db")
    try:
        await db.migrate()
        await db.migrate()
        version = await db.fetchone("PRAGMA user_version")
        assert version is not None and int(version[0]) == SCHEMA_VERSION
    finally:
        await db.close()


async def test_connect_creates_the_parent_folder(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "yourss.db"
    db = await connect(path)
    try:
        assert path.exists()
        await db.migrate()
    finally:
        await db.close()


async def test_secret_key_is_generated_persisted_and_never_logged(tmp_path: Path) -> None:
    path = tmp_path / "yourss.db"
    messages: list[str] = []
    sink = logger.add(messages.append, format="{message}")
    try:
        db = await connect(path)
        try:
            await db.migrate()
            secret = await db.secret_key()
            assert len(secret) >= 32
            assert await db.get_setting("secret_key") == secret
        finally:
            await db.close()

        reopened = await connect(path)
        try:
            assert await reopened.secret_key() == secret
        finally:
            await reopened.close()
    finally:
        logger.remove(sink)
    assert secret not in "".join(messages)


async def test_configured_secret_wins_without_being_stored(tmp_path: Path) -> None:
    db = await connect(tmp_path / "yourss.db")
    try:
        await db.migrate()
        assert await db.secret_key(SecretStr("configured-secret")) == "configured-secret"
        assert await db.get_setting("secret_key") is None
    finally:
        await db.close()


async def test_database_and_sidecars_are_private(tmp_path: Path) -> None:
    path = tmp_path / "yourss.db"
    db = await connect(path)
    try:
        await db.migrate()
        await db.execute("INSERT INTO settings (key, value) VALUES ('probe', '1')")
        await db.commit()
        for suffix in ("", "-wal", "-shm"):
            assert_private(Path(f"{path}{suffix}"))
    finally:
        await db.close()
