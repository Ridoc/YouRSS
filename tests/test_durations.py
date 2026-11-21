"""Video durations and the Like on YouTube link (C5.I2)."""

from typing import TYPE_CHECKING

from pytest import MonkeyPatch

from yourss.routers.jinja import jinja_env
from yourss.routers.utils import duration_text, enrich_durations
from yourss.settings import current_config
from yourss.youtube import VideoDescription
from yourss.youtube.scraping.items import duration_seconds

if TYPE_CHECKING:
    from yourss.db import Database


def test_duration_parsing() -> None:
    assert duration_seconds("4:05") == 245
    assert duration_seconds("1:02:03") == 3723
    assert duration_seconds("1 hour, 2 minutes, 3 seconds") == 3723
    assert duration_seconds(None) is None
    assert duration_seconds("live") is None
    assert duration_text(245) == "4:05"
    assert duration_text(3723) == "1:02:03"
    assert duration_text(None) == ""


def test_video_card_shows_duration_and_like() -> None:
    html = jinja_env.get_template("partials/videos.jinja-html").render(
        videos=[VideoDescription(video_id="dQw4w9WgXcQ", title="A video", duration_seconds=245)],
        link_prefix="/?",
    )
    assert "4:05" in html
    assert "Like on YouTube" in html


async def test_enrich_uses_the_cached_duration(db: "Database", monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(current_config, "video_durations", True)
    await db.execute(
        "INSERT OR REPLACE INTO video_durations (video_id, seconds, source, fetched_at) VALUES (?, 245, 'test', 0)",
        ("dQw4w9WgXcQ",),
    )
    await db.commit()
    videos = [
        VideoDescription(video_id="dQw4w9WgXcQ", title="A video"),
        VideoDescription(video_id="aaaaaaaaaaa", title="Unknown"),
    ]
    enriched = await enrich_durations(db, videos)
    assert enriched[0].duration_seconds == 245
    assert enriched[1].duration_seconds is None


async def test_enrich_is_a_no_op_when_disabled(db: "Database", monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(current_config, "video_durations", False)
    await db.execute(
        "INSERT OR REPLACE INTO video_durations (video_id, seconds, source, fetched_at) VALUES (?, 245, 'test', 0)",
        ("dQw4w9WgXcQ",),
    )
    await db.commit()
    videos = [VideoDescription(video_id="dQw4w9WgXcQ", title="A video")]
    assert await enrich_durations(db, videos) == videos
