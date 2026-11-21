"""Background refresh of every page, from the DB or the legacy users file."""

import asyncio
from datetime import timedelta
from time import monotonic, time

from loguru import logger

from .async_utils import Kind, State, SubscriptionStatus, async_fetch
from .db import Database
from .security import read_config
from .settings import current_config
from .subscriptions import list_warmup_pages
from .youtube import YoutubeApi


async def _warm_users_file() -> None:
    """Legacy path used by unit tests and by a deployment still on the YAML file."""
    config = read_config()
    if config is None:
        logger.warning("Warm-up: no pages to refresh")
        return
    start = monotonic()
    api = YoutubeApi()
    done: set[str] = set()
    failed = 0
    pages = [page for page in config.pages if page.warmup]
    for page in pages:
        names = [name for name in page.channels if name not in done]
        done.update(names)
        statuses = await async_fetch(names, api=api, refresh=True)
        failed += sum(status.state == State.FAILED for status in statuses)
    channels = len(done)
    logger.info(
        "Warm-up: {} pages ({} skipped), {} channels, {} failed, {:.1f}s",
        len(pages),
        len(config.pages) - len(pages),
        channels,
        failed,
        monotonic() - start,
    )


async def _record_status(db: Database, status: SubscriptionStatus) -> None:
    now = int(time())
    last_ok = now if status.state is State.OK else None
    await db.execute(
        """
        INSERT INTO warmup_status (channel, state, last_ok_at, last_error, checked_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(channel) DO UPDATE SET
            state = excluded.state,
            last_ok_at = COALESCE(excluded.last_ok_at, warmup_status.last_ok_at),
            last_error = excluded.last_error,
            checked_at = excluded.checked_at
        """,
        (status.id, status.state.value, last_ok, status.reason, now),
    )
    await db.commit()


async def _warm_durations(db: Database, api: YoutubeApi, channels: set[str]) -> None:
    now = int(time())
    ttl = int(current_config.duration_ttl.total_seconds())
    for channel in channels:
        last = await db.get_setting(f"durations:{channel}")
        if last is not None and now - int(last) < ttl:
            continue
        try:
            page = await api.get_channel_page(channel, "videos")
            for video in page.videos().videos:
                if video.duration_seconds is None:
                    continue
                await db.execute(
                    """
                    INSERT INTO video_durations (video_id, seconds, source, fetched_at)
                    VALUES (?, ?, 'channel-page', ?)
                    ON CONFLICT(video_id) DO UPDATE SET
                        seconds = excluded.seconds,
                        source = excluded.source,
                        fetched_at = excluded.fetched_at
                    """,
                    (video.video_id, video.duration_seconds, now),
                )
            await db.set_setting(f"durations:{channel}", str(now))
            await db.commit()
        except Exception as error:
            logger.warning("Warm-up: durations for {} failed: {!r}", channel, error)


async def _warm_db(db: Database) -> None:
    rows = await list_warmup_pages(db)
    if not rows:
        logger.warning("Warm-up: no pages to refresh")
        return
    start = monotonic()
    api = YoutubeApi()
    pages: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        pages.setdefault((str(row["username"]), str(row["name"])), []).append(str(row["ref"]))
    done: set[str] = set()
    failed = 0
    channels: set[str] = set()
    for names in pages.values():
        fresh = [name for name in names if name not in done]
        done.update(fresh)
        statuses = await async_fetch(fresh, api=api, refresh=True)
        for status in statuses:
            await _record_status(db, status)
            if status.kind == Kind.CHANNEL and status.state != State.FAILED:
                channels.add(status.id)
        failed += sum(status.state is State.FAILED for status in statuses)
    if current_config.video_durations:
        await _warm_durations(db, api, channels)
    logger.info(
        "Warm-up: {} pages, {} channels, {} failed, {:.1f}s",
        len(pages),
        len(done),
        failed,
        monotonic() - start,
    )


async def warm_round(db: Database | None = None) -> None:
    """Refresh every warmed page; the DB path also records states and durations."""
    if db is None:
        await _warm_users_file()
    else:
        await _warm_db(db)


def warm_interval(ttl: timedelta) -> timedelta:
    """A round a minute before the feeds expire, so that a visit never finds a stale one."""
    return max(ttl - timedelta(minutes=1), ttl / 2)


async def warm_forever(interval: timedelta, db: Database | None = None) -> None:
    """A round now, then one per interval; a round that fails is logged and the next one still comes."""
    while True:
        try:
            if db is None:
                await warm_round()
            else:
                await warm_round(db)
        except Exception as error:
            logger.exception("Warm-up round failed: {}", error)
        await asyncio.sleep(interval.total_seconds())
