"""Pages, subscriptions and the one-time users.yaml import."""

import asyncio
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import yaml  # type: ignore[import-untyped]
from loguru import logger

from yourss.db import Database
from yourss.schema import AppSettings

PAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
PUBLISH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
IMPORT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class SubscriptionError(ValueError):
    """Invalid page/subscription input."""


class LimitError(SubscriptionError):
    """A per-user or per-page cap was exceeded."""


class DuplicateError(SubscriptionError):
    """The page, publish name or subscription already exists."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"users file: {field} must be a non-empty string")
    return value.strip()


def _page_rows(payload: object) -> tuple[list[dict[str, Any]], set[str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("users"), list):
        raise RuntimeError("users file: expected a users list")
    users: list[dict[str, Any]] = []
    publishes: set[str] = set()
    names: set[str] = set()
    for raw_user in payload["users"]:
        if not isinstance(raw_user, dict):
            raise RuntimeError("users file: each user must be a mapping")
        name = _require_str(raw_user.get("name"), "user.name")
        if not IMPORT_NAME_RE.match(name):
            raise RuntimeError(f"users file: invalid user name {name}")
        key = name.casefold()
        if key in names:
            raise RuntimeError(f"users file: duplicate user {name}")
        names.add(key)
        raw_pages = raw_user.get("pages", [])
        if not isinstance(raw_pages, list):
            raise RuntimeError(f"users file: pages of {name} must be a list")
        pages: list[dict[str, Any]] = []
        page_names: set[str] = set()
        for raw_page in raw_pages:
            if not isinstance(raw_page, dict):
                raise RuntimeError(f"users file: each page of {name} must be a mapping")
            page_name = _require_str(raw_page.get("name"), "page.name")
            if not IMPORT_NAME_RE.match(page_name):
                raise RuntimeError(f"users file: invalid page name {page_name}")
            if page_name.casefold() in page_names:
                raise RuntimeError(f"users file: duplicate page {name}/{page_name}")
            page_names.add(page_name.casefold())
            publish = raw_page.get("publish")
            if publish is not None:
                publish = _require_str(publish, "page.publish")
                if not PUBLISH_RE.match(publish):
                    raise RuntimeError(f"users file: invalid publish name {publish}")
                if publish.casefold() in publishes:
                    raise RuntimeError(f"users file: duplicate publish name {publish}")
                publishes.add(publish.casefold())
            channels = raw_page.get("channels", [])
            if not isinstance(channels, list):
                raise RuntimeError(f"users file: channels of {name}/{page_name} must be a list")
            refs = [_require_str(ref, "channel") for ref in channels]
            pages.append(
                {
                    "name": page_name,
                    "publish": publish,
                    "warmup": bool(raw_page.get("warmup", True)),
                    "refs": refs,
                }
            )
        password = raw_user.get("password")
        if password is not None and not isinstance(password, dict):
            raise RuntimeError(f"users file: password of {name} must be a mapping")
        users.append({"name": name, "pages": pages, "password": password})
    return users, publishes


def _subscription_kind(ref: str) -> str:
    return "playlist" if ref.upper().startswith("PL") else "channel"


async def import_users_file(db: Database, settings: AppSettings) -> None:
    """Import the legacy users.yaml once; never move or rewrite the source file."""
    if not settings.users_file:
        return
    if await db.get_setting("users_imported_at"):
        return
    count = await db.fetchone("SELECT COUNT(*) FROM users")
    if count is not None and int(count[0]) > 0:
        # WHY: leave the marker unset; removing the pre-created users keeps the import retryable.
        logger.warning("legacy users file skipped: users already exist")
        return
    path = Path(settings.users_file)
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"users file {path} cannot be read: {exc}") from exc
    if "REPLACE_ME" in text:
        raise RuntimeError("users file still contains REPLACE_ME")
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"users file {path} is not valid YAML: {exc}") from exc
    users, _ = _page_rows(payload)
    pages_total = sum(len(user["pages"]) for user in users)
    subs_total = sum(len(page["refs"]) for user in users for page in user["pages"])
    if settings.import_expect_pages and pages_total < settings.import_expect_pages:
        raise RuntimeError(f"users file has {pages_total} pages, expected at least {settings.import_expect_pages}")
    if settings.import_expect_subs and subs_total < settings.import_expect_subs:
        raise RuntimeError(
            f"users file has {subs_total} subscriptions, expected at least {settings.import_expect_subs}"
        )
    async with db.transaction():
        for user in users:
            password = user.get("password")
            password = password if isinstance(password, dict) else None
            cursor = await db.execute(
                """
                INSERT INTO users (email, username, display_name, role, created_at,
                                   legacy_password_hash, legacy_password_method)
                VALUES (NULL, ?, ?, 'user', ?, ?, ?)
                """,
                (
                    user["name"],
                    user["name"],
                    now_iso(),
                    password.get("value") if password else None,
                    password.get("method") if password else None,
                ),
            )
            user_id = int(cursor.lastrowid or 0)
            for position, page in enumerate(user["pages"]):
                page_cursor = await db.execute(
                    """
                    INSERT INTO pages (user_id, name, publish, warmup, position, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        page["name"],
                        page["publish"],
                        int(bool(page["warmup"])),
                        position,
                        now_iso(),
                        now_iso(),
                    ),
                )
                page_id = int(page_cursor.lastrowid or 0)
                seen: set[str] = set()
                for sub_position, ref in enumerate(page["refs"]):
                    canonical = ref
                    if canonical.casefold() in seen:
                        continue
                    seen.add(canonical.casefold())
                    await db.execute(
                        """
                        INSERT INTO subscriptions
                            (page_id, ref, kind, canonical_id, position, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            page_id,
                            ref,
                            _subscription_kind(ref),
                            canonical,
                            sub_position,
                            now_iso(),
                        ),
                    )
        await db.execute(
            "INSERT INTO settings (key, value) VALUES ('users_imported_at', ?)",
            (now_iso(),),
        )


async def list_pages(db: Database, user_id: int) -> list[aiosqlite.Row]:
    return await db.fetchall(
        "SELECT id, user_id, name, publish, warmup, position FROM pages WHERE user_id = ? ORDER BY position, name",
        (user_id,),
    )


async def get_page(db: Database, user_id: int, name: str) -> aiosqlite.Row | None:
    return await db.fetchone(
        "SELECT id, user_id, name, publish, warmup, position FROM pages WHERE user_id = ? AND name = ?",
        (user_id, name),
    )


async def get_page_by_publish(db: Database, publish: str) -> aiosqlite.Row | None:
    return await db.fetchone(
        "SELECT id, user_id, name, publish, warmup, position FROM pages WHERE publish = ?",
        (publish,),
    )


async def count_pages(db: Database, user_id: int) -> int:
    row = await db.fetchone("SELECT COUNT(*) FROM pages WHERE user_id = ?", (user_id,))
    return int(row[0]) if row is not None else 0


async def create_page(
    db: Database,
    settings: AppSettings,
    user_id: int,
    name: str,
    *,
    publish: str | None = None,
    warmup: bool = True,
) -> aiosqlite.Row:
    name = name.strip()
    if not PAGE_NAME_RE.match(name):
        raise SubscriptionError("page name must be 1-64 letters, digits, spaces, dots, dashes or underscores")
    if publish is not None and not PUBLISH_RE.match(publish):
        raise SubscriptionError("publish name must be 1-32 letters, digits, dashes or underscores")
    try:
        async with db.transaction():
            position = await count_pages(db, user_id)
            if position >= settings.max_pages_per_user:
                raise LimitError(f"page limit of {settings.max_pages_per_user} reached")
            await db.execute(
                """
                INSERT INTO pages (user_id, name, publish, warmup, position, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, name, publish or None, int(warmup), position, now_iso(), now_iso()),
            )
    except sqlite3.IntegrityError as exc:
        raise DuplicateError("page or publish name already exists") from exc
    page = await get_page(db, user_id, name)
    if page is None:
        raise SubscriptionError("page was not created")
    return page


async def rename_page(db: Database, user_id: int, old_name: str, new_name: str) -> None:
    new_name = new_name.strip()
    if not PAGE_NAME_RE.match(new_name):
        raise SubscriptionError("page name must be 1-64 letters, digits, spaces, dots, dashes or underscores")
    try:
        cursor = await db.execute(
            "UPDATE pages SET name = ?, updated_at = ? WHERE user_id = ? AND name = ?",
            (new_name, now_iso(), user_id, old_name),
        )
        await db.commit()
    except sqlite3.IntegrityError as exc:
        raise DuplicateError("page name already exists") from exc
    if cursor.rowcount == 0:
        raise SubscriptionError("page not found")


async def delete_page(db: Database, user_id: int, name: str) -> None:
    cursor = await db.execute("DELETE FROM pages WHERE user_id = ? AND name = ?", (user_id, name))
    await db.commit()
    if cursor.rowcount == 0:
        raise SubscriptionError("page not found")


async def set_warmup(db: Database, user_id: int, name: str, enabled: bool) -> None:
    cursor = await db.execute(
        "UPDATE pages SET warmup = ?, updated_at = ? WHERE user_id = ? AND name = ?",
        (int(enabled), now_iso(), user_id, name),
    )
    await db.commit()
    if cursor.rowcount == 0:
        raise SubscriptionError("page not found")


async def set_publish(db: Database, user_id: int, name: str, publish: str | None) -> None:
    if publish is not None and not PUBLISH_RE.match(publish):
        raise SubscriptionError("publish name must be 1-32 letters, digits, dashes or underscores")
    try:
        cursor = await db.execute(
            "UPDATE pages SET publish = ?, updated_at = ? WHERE user_id = ? AND name = ?",
            (publish or None, now_iso(), user_id, name),
        )
        await db.commit()
    except sqlite3.IntegrityError as exc:
        raise DuplicateError("publish name already exists") from exc
    if cursor.rowcount == 0:
        raise SubscriptionError("page not found")


async def list_subscriptions(db: Database, page_id: int) -> list[aiosqlite.Row]:
    return await db.fetchall(
        "SELECT id, page_id, ref, kind, canonical_id, position "
        "FROM subscriptions WHERE page_id = ? ORDER BY position, id",
        (page_id,),
    )


async def add_subscription(
    db: Database,
    settings: AppSettings,
    page_id: int,
    ref: str,
    canonical_id: str | None = None,
    *,
    kind: str | None = None,
) -> aiosqlite.Row:
    ref = ref.strip()
    if not ref:
        raise SubscriptionError("subscription reference is empty")
    if kind is None:
        kind = _subscription_kind(ref)
    if kind not in ("channel", "playlist"):
        raise SubscriptionError("subscription kind must be channel or playlist")
    try:
        async with db.transaction():
            count = await db.fetchone("SELECT COUNT(*) FROM subscriptions WHERE page_id = ?", (page_id,))
            if count is not None and int(count[0]) >= settings.max_page_subscriptions:
                raise LimitError(f"subscription limit of {settings.max_page_subscriptions} reached")
            row = await db.fetchone(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM subscriptions WHERE page_id = ?",
                (page_id,),
            )
            position = int(row[0]) if row is not None else 0
            cursor = await db.execute(
                """
                INSERT INTO subscriptions (page_id, ref, kind, canonical_id, position, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (page_id, ref, kind, canonical_id or ref, position, now_iso()),
            )
    except sqlite3.IntegrityError as exc:
        raise DuplicateError("subscription already exists") from exc
    sub = await db.fetchone(
        "SELECT id, page_id, ref, kind, canonical_id, position FROM subscriptions WHERE id = ?",
        (cursor.lastrowid,),
    )
    if sub is None:
        raise SubscriptionError("subscription was not created")
    return sub


async def delete_subscription(db: Database, page_id: int, subscription_id: int) -> None:
    cursor = await db.execute("DELETE FROM subscriptions WHERE page_id = ? AND id = ?", (page_id, subscription_id))
    await db.commit()
    if cursor.rowcount == 0:
        raise SubscriptionError("subscription not found")


async def reorder_subscriptions(db: Database, page_id: int, ids: list[int]) -> None:
    existing = await list_subscriptions(db, page_id)
    existing_ids = {int(row["id"]) for row in existing}
    if set(ids) != existing_ids or len(ids) != len(existing_ids):
        raise SubscriptionError("reorder must list every subscription exactly once")
    async with db.transaction():
        for position, subscription_id in enumerate(ids):
            await db.execute(
                "UPDATE subscriptions SET position = ? WHERE page_id = ? AND id = ?",
                (position, page_id, subscription_id),
            )


async def list_warmup_pages(db: Database) -> list[aiosqlite.Row]:
    return await db.fetchall(
        """
        SELECT u.username, p.name, p.publish, s.ref, s.kind, s.canonical_id
        FROM pages p
        JOIN users u ON u.id = p.user_id
        JOIN subscriptions s ON s.page_id = p.id
        WHERE p.warmup = 1 AND u.disabled_at IS NULL
        ORDER BY u.username, p.position, s.position
        """
    )
