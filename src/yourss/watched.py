"""Server-side watched state for signed-in accounts."""

from time import time

from yourss.db import Database

WATCHED_MAX = 3000


async def get_watched(db: Database, user_id: int) -> dict[str, int]:
    rows = await db.fetchall(
        "SELECT video_id, watched_at FROM watched WHERE user_id = ? ORDER BY watched_at", (user_id,)
    )
    return {str(row["video_id"]): int(row["watched_at"]) for row in rows}


async def set_watched(
    db: Database,
    user_id: int,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    now: int | None = None,
) -> dict[str, int]:
    timestamp = int(time() if now is None else now)
    async with db.transaction():
        for video_id in remove or []:
            await db.execute(
                "DELETE FROM watched WHERE user_id = ? AND video_id = ?", (user_id, video_id)
            )
        for video_id in add or []:
            await db.execute(
                "INSERT INTO watched (user_id, video_id, watched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, video_id) DO UPDATE SET watched_at = excluded.watched_at",
                (user_id, video_id, timestamp),
            )
        count = await db.fetchone("SELECT COUNT(*) FROM watched WHERE user_id = ?", (user_id,))
        excess = (int(count[0]) if count is not None else 0) - WATCHED_MAX
        if excess > 0:
            await db.execute(
                "DELETE FROM watched WHERE rowid IN (SELECT rowid FROM watched WHERE user_id = ? "
                "ORDER BY watched_at LIMIT ?)",
                (user_id, excess),
            )
    return await get_watched(db, user_id)


async def import_watched(
    db: Database, user_id: int, values: dict[str, int], *, now: int | None = None
) -> dict[str, int]:
    """Merge a local map once; localStorage milliseconds become server seconds."""
    if not values:
        return await get_watched(db, user_id)
    timestamp = int(time() if now is None else now)
    async with db.transaction():
        row = await db.fetchone("SELECT watched_imported_at FROM users WHERE id = ?", (user_id,))
        if row is None or row["watched_imported_at"] is not None:
            return await get_watched(db, user_id)
        for video_id, value in values.items():
            seconds = int(value / 1000) if value > 10_000_000_000 else int(value)
            await db.execute(
                "INSERT INTO watched (user_id, video_id, watched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, video_id) DO UPDATE SET watched_at = excluded.watched_at",
                (user_id, video_id, seconds or timestamp),
            )
        await db.execute(
            "UPDATE users SET watched_imported_at = ? WHERE id = ?", (str(timestamp), user_id)
        )
        count = await db.fetchone("SELECT COUNT(*) FROM watched WHERE user_id = ?", (user_id,))
        excess = (int(count[0]) if count is not None else 0) - WATCHED_MAX
        if excess > 0:
            await db.execute(
                "DELETE FROM watched WHERE rowid IN (SELECT rowid FROM watched WHERE user_id = ? "
                "ORDER BY watched_at LIMIT ?)",
                (user_id, excess),
            )
    return await get_watched(db, user_id)
