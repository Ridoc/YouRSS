"""Subscription reference parsing for the account page."""

from pytest import raises

from yourss.routers.account import _parse_youtube_ref, _resolve_ref
from yourss.subscriptions import SubscriptionError

CHANNEL = "UCVooVnzQxPSTXTMzSi1s6uw"


def test_parse_youtube_urls() -> None:
    assert _parse_youtube_ref(f"https://www.youtube.com/channel/{CHANNEL}") == ("channel", CHANNEL)
    assert _parse_youtube_ref(f"youtube.com/channel/{CHANNEL}/videos") == ("channel", CHANNEL)
    assert _parse_youtube_ref("https://www.youtube.com/@mkbhd") == ("handle", "@mkbhd")
    assert _parse_youtube_ref("youtube.com/@mkbhd") == ("handle", "@mkbhd")
    assert _parse_youtube_ref("https://www.youtube.com/playlist?list=PL1234567890") == ("playlist", "PL1234567890")
    assert _parse_youtube_ref("https://youtu.be/abcdefghijk") == ("video", "abcdefghijk")
    assert _parse_youtube_ref("https://www.youtube.com/watch?v=abcdefghijk&list=PL1234567890") == (
        "video",
        "abcdefghijk",
    )
    assert _parse_youtube_ref("@mkbhd") is None
    assert _parse_youtube_ref("https://example.com/@mkbhd") is None


async def test_resolve_accepts_raw_ids_and_urls() -> None:
    assert await _resolve_ref(CHANNEL) == ("channel", CHANNEL)
    assert await _resolve_ref(f"https://www.youtube.com/channel/{CHANNEL}") == ("channel", CHANNEL)
    assert await _resolve_ref("https://www.youtube.com/playlist?list=PL1234567890") == (
        "playlist",
        "PL1234567890",
    )
    with raises(SubscriptionError):
        await _resolve_ref("https://example.com/@mkbhd")
