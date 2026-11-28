"""Account self-service: profile, export, pages and deletion (C3.I1)."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4

from bs4 import BeautifulSoup
from httpx import AsyncClient
from pytest import MonkeyPatch

from yourss import accounts
from yourss.async_utils import Kind, State, SubscriptionStatus
from yourss.settings import current_config
from yourss.youtube import ChannelDescription, Feed

if TYPE_CHECKING:
    from yourss.db import Database

SeedUser = Callable[..., Awaitable[int]]


def unique(prefix: str = "acct") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def login(client: AsyncClient, user_id: int, secret: str, version: int = 1) -> None:
    client.cookies.set(
        accounts.SESSION_COOKIE,
        accounts.create_session_cookie(secret, user_id, version, 3600),
    )


async def csrf(client: AsyncClient, path: str = "/account") -> str:
    response = await client.get(path)
    assert response.status_code == 200
    return str(client.cookies[accounts.CSRF_COOKIE])


async def test_profile_update_export_and_delete(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    username = unique()
    user_id = await seed_user(username)
    login(client, user_id, secret_key)
    token = await csrf(client)
    response = await client.post(
        "/account",
        data={"username": f"{username}-new", "display_name": "New Name", "csrf_token": token},
    )
    assert response.status_code == 303
    row = await db.fetchone("SELECT username, display_name FROM users WHERE id = ?", (user_id,))
    assert row is not None and tuple(row) == (f"{username}-new", "New Name")

    exported = await client.get("/account/export")
    assert exported.status_code == 200
    assert exported.json()["user"]["username"] == f"{username}-new"
    assert exported.json()["pages"] == []

    missing = await client.post("/account/delete", data={"confirm": "", "csrf_token": token})
    assert missing.status_code == 400
    deleted = await client.post("/account/delete", data={"confirm": "DELETE", "csrf_token": token})
    assert deleted.status_code == 303
    assert await db.fetchone("SELECT 1 FROM users WHERE id = ?", (user_id,)) is None


async def test_page_crud_from_the_account(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    user_id = await seed_user(unique())
    login(client, user_id, secret_key)
    token = await csrf(client)
    page = unique("Page")
    response = await client.post(
        "/account/pages",
        data={"name": page, "publish": "", "warmup": "on", "csrf_token": token},
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/account/pages/{page}"
    assert await db.fetchone("SELECT 1 FROM pages WHERE user_id = ? AND name = ?", (user_id, page)) is not None

    editor = await client.get(f"/account/pages/{page}")
    assert editor.status_code == 200
    assert page in editor.text

    renamed = page + " Renamed"
    response = await client.post(
        f"/account/pages/{page}",
        data={"new_name": renamed, "publish": unique("pub"), "warmup": "", "csrf_token": token},
    )
    assert response.status_code == 303
    row = await db.fetchone("SELECT name, warmup FROM pages WHERE user_id = ?", (user_id,))
    assert row is not None and row["name"] == renamed and row["warmup"] == 0

    response = await client.post(f"/account/pages/{renamed}/delete", data={"csrf_token": token})
    assert response.status_code == 303
    assert await db.fetchone("SELECT 1 FROM pages WHERE user_id = ?", (user_id,)) is None


async def test_add_and_remove_subscription_from_the_account(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    user_id = await seed_user(unique())
    login(client, user_id, secret_key)
    token = await csrf(client)
    page = unique("Subs")
    await client.post("/account/pages", data={"name": page, "csrf_token": token})
    response = await client.post(
        f"/account/pages/{page}/subscriptions",
        data={"ref": "UC" + "a" * 22, "csrf_token": token},
    )
    assert response.status_code == 303
    page_row = await db.fetchone("SELECT id FROM pages WHERE user_id = ? AND name = ?", (user_id, page))
    assert page_row is not None
    assert await db.fetchone("SELECT 1 FROM subscriptions WHERE page_id = ?", (int(page_row["id"]),)) is not None
    subscription = await db.fetchone("SELECT id FROM subscriptions WHERE page_id = ?", (int(page_row["id"]),))
    assert subscription is not None
    response = await client.post(
        f"/account/pages/{page}/subscriptions/{int(subscription['id'])}/delete",
        data={"csrf_token": token},
    )
    assert response.status_code == 303
    assert await db.fetchone("SELECT 1 FROM subscriptions WHERE page_id = ?", (int(page_row["id"]),)) is None


async def test_move_subscription_route_and_focus(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    user_id = await seed_user(unique("move"))
    login(client, user_id, secret_key)
    token = await csrf(client)
    page = unique("Move Page")
    assert (await client.post("/account/pages", data={"name": page, "csrf_token": token})).status_code == 303
    page_row = await db.fetchone("SELECT id FROM pages WHERE user_id = ? AND name = ?", (user_id, page))
    assert page_row is not None
    page_id = int(page_row["id"])
    refs = [("UC" + "a" * 22, "channel"), ("PL" + "b" * 32, "playlist"), ("UC" + "c" * 22, "channel")]
    now = datetime.now(UTC).isoformat()
    for position, (ref, kind) in enumerate(refs):
        await db.execute(
            "INSERT INTO subscriptions (page_id, ref, kind, canonical_id, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (page_id, ref, kind, ref, position, now),
        )
    await db.commit()
    first_row = await db.fetchone("SELECT id FROM subscriptions WHERE page_id = ? AND ref = ?", (page_id, refs[0][0]))
    last_row = await db.fetchone("SELECT id FROM subscriptions WHERE page_id = ? AND ref = ?", (page_id, refs[2][0]))
    assert first_row is not None and last_row is not None
    first_id = int(first_row["id"])
    last_id = int(last_row["id"])

    moved = await client.post(
        f"/account/pages/{quote(page)}/subscriptions/{last_id}/move",
        data={"direction": "up", "csrf_token": token},
    )
    assert moved.status_code == 303
    assert moved.headers["location"] == f"/account/pages/{quote(page)}?notice=moved#subscription-{last_id}"
    rows = await db.fetchall(
        "SELECT id, position FROM subscriptions WHERE page_id = ? ORDER BY position, id", (page_id,)
    )
    assert [int(row["id"]) for row in rows] == [last_id, int(rows[1]["id"]), first_id]
    assert [int(row["position"]) for row in rows] == [0, 1, 2]

    editor = await client.get(f"/account/pages/{quote(page)}?notice=moved")
    assert editor.status_code == 200
    assert "Order updated." in editor.text
    soup = BeautifulSoup(editor.text, "html.parser")
    moved_row = soup.select_one(f"#subscription-{last_id}")
    channels_heading = soup.select_one("h2#group-channels")
    assert moved_row is not None and channels_heading is not None
    assert moved_row["tabindex"] == "-1"
    assert channels_heading["tabindex"] == "-1"

    boundary = await client.post(
        f"/account/pages/{quote(page)}/subscriptions/{last_id}/move",
        data={"direction": "up", "csrf_token": token},
    )
    assert boundary.status_code == 303
    assert boundary.headers["location"] == f"/account/pages/{quote(page)}#subscription-{last_id}"

    invalid = await client.post(
        f"/account/pages/{quote(page)}/subscriptions/{last_id}/move",
        data={"direction": "left", "csrf_token": token},
    )
    assert invalid.status_code == 400
    bad_csrf = await client.post(
        f"/account/pages/{quote(page)}/subscriptions/{last_id}/move",
        data={"direction": "up", "csrf_token": "bad"},
    )
    assert bad_csrf.status_code == 403
    missing = await client.post(
        f"/account/pages/{quote(page)}/subscriptions/999999/move",
        data={"direction": "up", "csrf_token": token},
    )
    assert missing.status_code == 404

    other_page = unique("Other Move")
    assert (await client.post("/account/pages", data={"name": other_page, "csrf_token": token})).status_code == 303
    other_page_row = await db.fetchone("SELECT id FROM pages WHERE user_id = ? AND name = ?", (user_id, other_page))
    assert other_page_row is not None
    other_ref = "UC" + "d" * 22
    await db.execute(
        "INSERT INTO subscriptions (page_id, ref, kind, canonical_id, position, created_at) "
        "VALUES (?, ?, 'channel', ?, 0, ?)",
        (int(other_page_row["id"]), other_ref, other_ref, datetime.now(UTC).isoformat()),
    )
    await db.commit()
    other_subscription = await db.fetchone(
        "SELECT id FROM subscriptions WHERE page_id = ? AND ref = ?", (int(other_page_row["id"]), other_ref)
    )
    assert other_subscription is not None
    cross_page = await client.post(
        f"/account/pages/{quote(page)}/subscriptions/{int(other_subscription['id'])}/move",
        data={"direction": "up", "csrf_token": token},
    )
    assert cross_page.status_code == 404

    single_page = unique("Single Move")
    assert (await client.post("/account/pages", data={"name": single_page, "csrf_token": token})).status_code == 303
    single_row = await db.fetchone("SELECT id FROM pages WHERE user_id = ? AND name = ?", (user_id, single_page))
    assert single_row is not None
    single_ref = "UC" + "e" * 22
    await db.execute(
        "INSERT INTO subscriptions (page_id, ref, kind, canonical_id, position, created_at) "
        "VALUES (?, ?, 'channel', ?, 0, ?)",
        (int(single_row["id"]), single_ref, single_ref, datetime.now(UTC).isoformat()),
    )
    await db.commit()
    single_editor = await client.get(f"/account/pages/{quote(single_page)}")
    single_soup = BeautifulSoup(single_editor.text, "html.parser")
    assert single_soup.select("button[aria-label^='Move']") == []

    other_id = await seed_user(unique("other"))
    login(client, other_id, secret_key)
    foreign_token = await csrf(client)
    foreign = await client.post(
        f"/account/pages/{quote(page)}/subscriptions/{last_id}/move",
        data={"direction": "up", "csrf_token": foreign_token},
    )
    assert foreign.status_code == 404


async def test_editor_groups_named_failures_and_unresolved(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str, monkeypatch: MonkeyPatch
) -> None:
    username = unique("groups")
    user_id = await seed_user(username)
    login(client, user_id, secret_key)
    token = await csrf(client)
    page = unique("Groups")
    assert (await client.post("/account/pages", data={"name": page, "csrf_token": token})).status_code == 303
    page_row = await db.fetchone("SELECT id FROM pages WHERE user_id = ? AND name = ?", (user_id, page))
    assert page_row is not None
    page_id = int(page_row["id"])

    resolved_channel = "UC" + "a" * 22
    resolved_playlist = "PLw-vK1_d04zZCal3yMX_T23h5nDJ2toTk"
    named_failure = "UC" + "b" * 22
    unknown_channel = "UC" + "c" * 22
    unknown_playlist = "PL" + "b" * 32
    now = datetime.now(UTC).isoformat()
    for position, (ref, kind) in enumerate(
        [
            (resolved_channel, "channel"),
            (resolved_playlist, "playlist"),
            (named_failure, "channel"),
            (unknown_channel, "channel"),
            (unknown_playlist, "playlist"),
        ]
    ):
        await db.execute(
            "INSERT INTO subscriptions (page_id, ref, kind, canonical_id, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (page_id, ref, kind, ref, position, now),
        )
    await db.commit()

    feed = Feed.from_xml((Path(__file__).parent / "data" / f"{resolved_playlist}.xml").read_bytes())
    statuses = [
        SubscriptionStatus(
            name=resolved_channel,
            kind=Kind.CHANNEL,
            state=State.OK,
            channel=ChannelDescription(channel_id=resolved_channel, name="Resolved Channel"),
        ),
        SubscriptionStatus(name=resolved_playlist, kind=Kind.PLAYLIST, state=State.OK, feed=feed),
        SubscriptionStatus(
            name=named_failure,
            kind=Kind.CHANNEL,
            state=State.FAILED,
            channel=ChannelDescription(channel_id=named_failure, name="Named Failed"),
            reason="Youtube answered an error",
        ),
        SubscriptionStatus(
            name=unknown_channel, kind=Kind.CHANNEL, state=State.FAILED, reason="Youtube could not be reached"
        ),
        SubscriptionStatus(
            name=unknown_playlist, kind=Kind.PLAYLIST, state=State.FAILED, reason="Youtube did not answer in time"
        ),
    ]

    async def fake_fetch(names: list[str], api: object, *, refresh: bool = False) -> list[SubscriptionStatus]:
        assert names == [resolved_channel, resolved_playlist, named_failure, unknown_channel, unknown_playlist]
        return statuses

    monkeypatch.setattr("yourss.routers.account.async_fetch", fake_fetch)
    response = await client.get(f"/account/pages/{quote(page)}")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")

    channels_heading = soup.select_one("h2#group-channels")
    playlists_heading = soup.select_one("h2#group-playlists")
    unresolved_heading = soup.select_one("h2#group-unresolved")
    assert channels_heading is not None and playlists_heading is not None and unresolved_heading is not None
    assert channels_heading.get_text(" ", strip=True).startswith("Channels")
    assert playlists_heading.get_text(" ", strip=True).startswith("Playlists")
    assert unresolved_heading.get_text(" ", strip=True) == "Unresolved"
    channels = soup.find("section", attrs={"aria-labelledby": "group-channels"})
    playlists = soup.find("section", attrs={"aria-labelledby": "group-playlists"})
    unresolved = soup.find("section", attrs={"aria-labelledby": "group-unresolved"})
    assert channels is not None and playlists is not None and unresolved is not None
    channel_rows = channels.select("li.yourss-sub-row")
    assert len(channel_rows) == 2
    assert len(playlists.select("li.yourss-sub-row")) == 1
    assert "Resolved Channel" in channels.get_text()
    assert "Named Failed" in channels.get_text()
    assert channels.select_one(".yourss-state.is-failed") is not None
    assert feed.title in playlists.get_text()
    unresolved_refs = []
    for row in unresolved.select("li.yourss-sub-row"):
        ref_tag = row.select_one(".yourss-sub-ref")
        assert ref_tag is not None
        unresolved_refs.append(ref_tag.get_text(strip=True))
    assert unresolved_refs == sorted([unknown_channel, unknown_playlist])
    assert "name unavailable" in unresolved.get_text()
    assert "Youtube could not be reached" in unresolved.get_text()
    assert soup.select("button[data-direction]") == []
    first_move = channel_rows[0].select("button[aria-label^='Move']")
    last_move = channel_rows[-1].select("button[aria-label^='Move']")
    assert len(first_move) == 2 and first_move[0].has_attr("disabled") and not first_move[1].has_attr("disabled")
    assert len(last_move) == 2 and not last_move[0].has_attr("disabled") and last_move[1].has_attr("disabled")
    assert playlists.select("button[aria-label^='Move']") == []
    assert unresolved.select("button[aria-label^='Move']") == []
    assert soup.select(".yourss-card h2")[-1].get_text(" ", strip=True) == "Page settings"
    open_link = soup.select_one(".yourss-account-head a.yourss-btn")
    assert open_link is not None
    assert open_link["href"] == f"/u/{username}/{quote(page)}"


async def test_editor_add_error_notices_and_open_page_encoding(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str
) -> None:
    username = unique("editor")
    user_id = await seed_user(username)
    login(client, user_id, secret_key)
    token = await csrf(client)
    page = unique("Editor Page")
    assert (await client.post("/account/pages", data={"name": page, "csrf_token": token})).status_code == 303
    editor_page = await db.fetchone("SELECT id FROM pages WHERE user_id = ? AND name = ?", (user_id, page))
    assert editor_page is not None
    page_id = int(editor_page["id"])
    ref = "UC" + "d" * 22

    added = await client.post(f"/account/pages/{quote(page)}/subscriptions", data={"ref": ref, "csrf_token": token})
    assert added.status_code == 303
    location = added.headers["location"]
    assert "?notice=added#subscription-" in location
    subscription = await db.fetchone("SELECT id FROM subscriptions WHERE page_id = ? AND ref = ?", (page_id, ref))
    assert subscription is not None

    editor = await client.get(f"/account/pages/{quote(page)}?add_error=format&ref=bad+ref")
    assert editor.status_code == 200
    soup = BeautifulSoup(editor.text, "html.parser")
    assert "Use @handle, UC..., PL... or a YouTube link." in editor.text
    ref_input = soup.select_one("input[name=ref]")
    assert ref_input is not None
    assert ref_input["value"] == "bad ref"
    assert ref_input["aria-invalid"] == "true"

    duplicate = await client.post(f"/account/pages/{quote(page)}/subscriptions", data={"ref": ref, "csrf_token": token})
    assert duplicate.status_code == 303
    assert "add_error=duplicate" in duplicate.headers["location"]

    required = await client.post(f"/account/pages/{quote(page)}/subscriptions", data={"ref": "", "csrf_token": token})
    assert required.status_code == 303
    assert "add_error=required" in required.headers["location"]

    bad_ref = await client.post(
        f"/account/pages/{quote(page)}/subscriptions", data={"ref": "not a ref", "csrf_token": token}
    )
    assert bad_ref.status_code == 303
    bad_location = bad_ref.headers["location"]
    assert "add_error=format" in bad_location and "ref=not+a+ref" in bad_location
    bad_editor = await client.get(bad_location)
    bad_input = BeautifulSoup(bad_editor.text, "html.parser").select_one("input[name=ref]")
    assert bad_input is not None
    assert bad_input["value"] == "not a ref"

    unknown_notice = await client.get(f"/account/pages/{quote(page)}?notice=bogus")
    assert "Added to this page." not in unknown_notice.text
    assert "Removed from this page." not in unknown_notice.text

    removed = await client.post(
        f"/account/pages/{quote(page)}/subscriptions/{int(subscription['id'])}/delete",
        data={"csrf_token": token},
    )
    assert removed.status_code == 303
    assert removed.headers["location"] == f"/account/pages/{quote(page)}?notice=removed#group-channels"
    notice = await client.get(f"/account/pages/{quote(page)}?notice=removed")
    assert "Removed from this page." in notice.text

    open_page = await client.get(f"/account/pages/{quote(page)}")
    open_link = BeautifulSoup(open_page.text, "html.parser").select_one(".yourss-account-head a.yourss-btn")
    assert open_link is not None
    assert open_link["href"] == f"/u/{username}/{quote(page)}"


async def test_editor_cap_warning_and_disabled_follow(
    client: AsyncClient, db: "Database", seed_user: SeedUser, secret_key: str, monkeypatch: MonkeyPatch
) -> None:
    user_id = await seed_user(unique("cap"))
    login(client, user_id, secret_key)
    token = await csrf(client)
    page = unique("Cap")
    assert (await client.post("/account/pages", data={"name": page, "csrf_token": token})).status_code == 303
    page_row = await db.fetchone("SELECT id FROM pages WHERE user_id = ? AND name = ?", (user_id, page))
    assert page_row is not None
    await db.execute(
        "INSERT INTO subscriptions (page_id, ref, kind, canonical_id, position, created_at) "
        "VALUES (?, ?, 'channel', ?, 0, ?)",
        (int(page_row["id"]), "UC" + "e" * 22, "UC" + "e" * 22, datetime.now(UTC).isoformat()),
    )
    await db.commit()
    monkeypatch.setattr(current_config, "max_page_subscriptions", 1)

    response = await client.get(f"/account/pages/{quote(page)}")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    assert "maximum of 1 channels and playlists" in response.text
    follow_button = soup.select_one(".yourss-follow-card button[type=submit]")
    assert follow_button is not None and follow_button.has_attr("disabled")
    assert soup.select_one("li.yourss-sub-row button[type=submit]") is not None

    blocked = await client.post(
        f"/account/pages/{quote(page)}/subscriptions",
        data={"ref": "UC" + "f" * 22, "csrf_token": token},
    )
    assert blocked.status_code == 303
    assert "add_error=cap" in blocked.headers["location"]
