"""SQLite access, schema migrations and the instance signing key."""

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger
from pydantic import SecretStr

SCHEMA_VERSION = 1

# WHY: one ordered script, applied once; PRAGMA user_version makes it idempotent without Alembic.
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT COLLATE NOCASE UNIQUE,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    created_at TEXT NOT NULL,
    disabled_at TEXT,
    session_version INTEGER NOT NULL DEFAULT 1,
    legacy_password_hash TEXT,
    legacy_password_method TEXT,
    watched_imported_at TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    publish TEXT COLLATE NOCASE UNIQUE,
    warmup INTEGER NOT NULL DEFAULT 1 CHECK (warmup IN (0, 1)),
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    ref TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('channel', 'playlist')),
    canonical_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(page_id, canonical_id)
);

CREATE TABLE IF NOT EXISTS watched (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    video_id TEXT NOT NULL,
    watched_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, video_id)
);

CREATE TABLE IF NOT EXISTS login_tokens (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE,
    token_hash TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL CHECK (purpose IN ('login')),
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    created_at INTEGER NOT NULL,
    ip_hash TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    ip_hash TEXT
);

CREATE TABLE IF NOT EXISTS video_durations (
    video_id TEXT PRIMARY KEY,
    seconds INTEGER NOT NULL,
    source TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS warmup_status (
    channel TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    last_ok_at INTEGER,
    last_error TEXT,
    checked_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pages_user_id ON pages(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_page_position ON subscriptions(page_id, position);
CREATE INDEX IF NOT EXISTS idx_login_tokens_email_created ON login_tokens(email, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_warmup_status_state ON warmup_status(state);

PRAGMA user_version = 1;
"""

Params = tuple[Any, ...] | Mapping[str, Any]


class Database:
    """One SQLite connection, its PRAGMAs and the instance key stored in `settings`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: aiosqlite.Connection | None = None
        self._secret: str | None = None
        # WHY: one shared connection means a transaction must not interleave with another one.
        self._write_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("database is not connected")
        return self._connection

    async def connect(self) -> None:
        """Open the file with WAL, foreign keys, a busy timeout and owner-only permissions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # WHY: SQLite creates -wal/-shm with the process umask; 077 keeps them private from the first byte.
        previous_umask = os.umask(0o077)
        try:
            connection = await aiosqlite.connect(self.path, isolation_level=None)
        finally:
            os.umask(previous_umask)
        connection.row_factory = aiosqlite.Row
        self._connection = connection
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA synchronous = NORMAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        cursor = await connection.execute("PRAGMA journal_mode = WAL")
        await cursor.fetchone()
        await cursor.close()
        self._make_private()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def migrate(self) -> None:
        """Apply the ordered schema once; a newer database fails closed."""
        row = await self.fetchone("PRAGMA user_version")
        version = int(row[0]) if row is not None else 0
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"database schema version {version} is newer than this application ({SCHEMA_VERSION})")
        if version < SCHEMA_VERSION:
            await self.connection.executescript(SCHEMA)
            await self.connection.commit()
        self._make_private()

    async def execute(self, sql: str, parameters: Params = ()) -> aiosqlite.Cursor:
        return await self.connection.execute(sql, parameters)

    async def executemany(self, sql: str, parameters: Any) -> aiosqlite.Cursor:
        return await self.connection.executemany(sql, parameters)

    async def fetchone(self, sql: str, parameters: Params = ()) -> aiosqlite.Row | None:
        cursor = await self.connection.execute(sql, parameters)
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()

    async def fetchall(self, sql: str, parameters: Params = ()) -> list[aiosqlite.Row]:
        cursor = await self.connection.execute(sql, parameters)
        try:
            return list(await cursor.fetchall())
        finally:
            await cursor.close()

    async def commit(self) -> None:
        await self.connection.commit()

    async def rollback(self) -> None:
        await self.connection.rollback()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """One write transaction; the caller commits by leaving the block and rolls back on an exception."""
        async with self._write_lock:
            await self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                await self.connection.rollback()
                raise
            else:
                await self.connection.commit()

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = await self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return str(row[0]) if row is not None else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.commit()

    async def secret_key(self, configured: SecretStr | None = None) -> str:
        """The env key when set, else a generated key persisted once; the value is never logged."""
        if configured is not None:
            value = configured.get_secret_value()
            if value:
                if len(value) < 32:
                    logger.warning("YOURSS_SECRET_KEY is shorter than 32 characters")
                return value
        if self._secret is None:
            stored = await self.get_setting("secret_key")
            if stored is None:
                candidate = secrets.token_urlsafe(48)
                # WHY: two workers may race on first boot; only one row wins, then both adopt it.
                await self.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                    ("secret_key", candidate),
                )
                await self.commit()
                stored = await self.get_setting("secret_key") or candidate
                logger.warning("YOURSS_SECRET_KEY is unset; generated a key and stored it in the settings table")
            self._secret = stored
        return self._secret

    def _make_private(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            sidecar = Path(f"{self.path}{suffix}")
            if sidecar.exists():
                sidecar.chmod(0o600)


async def connect(path: Path) -> Database:
    """Open and configure one database connection."""
    database = Database(path)
    await database.connect()
    return database
