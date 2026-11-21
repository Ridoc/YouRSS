![Github](https://img.shields.io/github/tag/Ridoc/YouRSS.svg)

# YouRSS+

> An advanced YouTube RSS feed viewer: the latest videos of the channels you chose, on one page. YouRSS+ keeps the RSS-first, pass-through design while account, self-service subscription and watched-sync work lands in this fork; the fixes are tracked in this repository's issues.

**YouRSS+** is a fork of [essembeh/YouRSS](https://github.com/essembeh/YouRSS), which remains the upstream project. The Python package and import name stay `yourss` so existing deployments and data files keep working.

![YouRSS+ on desktop and on mobile: the same channel, opened inside its folder (neon theme)](./images/yourss_hero.png)

<details>
<summary>More screenshots</summary>

| The player takes the whole row | A channel, opened inside its folder |
| --- | --- |
| ![Player](./images/yourss_player.png) | ![Channel view](./images/yourss_channel.png) |

<p align="center">
  <img src="./images/yourss_mobile_menu.png" alt="YouRSS+ on mobile: the folders and their channels" width="30%">
  <img src="./images/yourss_mobile_settings.png" alt="YouRSS+ on mobile: the settings" width="30%">
</p>

</details>

## 🚀 Try the demo

**https://yourss.onrender.com**

> This is the upstream project's demo. It is a public instance, shared by everyone and hosted on a free plan: it sleeps when nobody uses it, so the first page may take about a minute to show up. It comes without any guarantee and can be limited or stopped at any time: to follow your channels every day, [install your own](#-install).

## ✨ Features

### 🧱 Build your own page

No account: your subscriptions are the address of your page.

| 1. Start from the home page | 2. Your page is created | 3. Add the rest, then bookmark |
| --- | --- | --- |
| ![The home page offers three ways to start](./images/yourss_home.png) | ![The page just created](./images/yourss_welcome.png) | ![Adding a channel or a playlist](./images/yourss_add.png) |

- the home page offers three ways to start: a demo page, ready to edit, a blank page made from your first channel, or the last page opened in this browser
- then the **+** next to *Subscriptions* adds the others
- a channel is added by its handle (`@name`), its *channel_id*, its address or the address of one of its videos; a playlist by its id or its address
- a subscription is removed from the sidebar
- each change gives a new address, made of ids only (example https://yourss.domain.tld/UCVooVnzQxPSTXTMzSi1s6uw,PLw-vK1_d04zZCal3yMX_T23h5nDJ2toTk): bookmark it, share it, open it on another device. Nothing is stored on the server

The administrator of an instance can also declare *users*, who hold *pages*, named lists of channels and playlists shown as folders (`/u/<user>/<page>`) and optionally published under a short name (`/p/<name>`), and can turn custom pages off.

### 📰 One page for all your subscriptions

A page shows the last 15 videos of each channel and what the feed of each playlist holds, sorted by date.

Turn on *Show new videos* in the settings and the videos you played for a few seconds are marked as watched: on the next visit a `NEW` marker and a counter per channel show what you have not seen yet, and an *All / New* switch hides the rest. This is stored in your browser only.

### 🎧 Listen to a page, in the car or while you work

<p align="center">
  <img src="./images/yourss_mobile_listen.png" alt="The listen mode on a phone: the whole screen, finger sized commands" height="420">
  <img src="./images/yourss_listen.png" alt="The listen mode on desktop: a mini player over the page" height="420">
</p>

*Listen* plays the videos of the page one after the other, as a playlist of music.

- **on a phone** it takes the whole screen, with finger sized commands for a phone on a car mount: previous, play / pause, next, −30 / +30 seconds (hold them to jump further, a bubble shows how far), the channel and the title, the next one. The screen stays on while it plays (an embedded Youtube player stops when the screen locks), and a button shows the thumbnail instead of the video
- **on desktop** it is a mini player in a corner, the page stays below: a click on a video plays it there
- start it with the headphones button, the `l` key or 🎧 in the mini player, or add `?listen` to the address of a page: bookmark it, or add it to the home screen of your phone
- *Listen* at the top of the player chooses what to listen to: the whole page, or a channel of the page and one of its tabs (Recent, Videos, Shorts, Streams). Opening another folder or channel keeps listening: its videos become the playlist
- ✕ or `Esc` stops it

### ▶️ Play in the page

Click a thumbnail to play: the player takes the whole row, or the whole screen on a phone. A link on each card opens the video on Youtube. To play the videos one after the other, use *Listen*.

### 📺 Keep watching while you browse

Turn on the *Mini player* in the settings: when you scroll away, the video keeps playing in a corner of the screen, without a gap, while you browse the rest of the page. Its card shows where it comes from, a click (or ↩) brings you back to it and 🎧 turns it into the listen player. Without it, the player is closed as soon as its card leaves the screen. Opening another folder or channel closes it.

### 📂 Channels and playlists, inside the page

Click a channel in the sidebar to browse it without leaving your page: its recent videos, all its videos, its shorts, its streams and its playlists. A playlist opens the same way, with two tabs: Recent, what its feed holds, and All, every video of the list in its order; a playlist picked in a channel opens on All, under the header of the channel, with a button back to its playlists.

### 🎁 And also

- three themes (neon, dark, light), to pick on the home page or in the settings, and three density levels
- thumbnails: the cover chosen by the channel, or a frame from the middle of the video, less clickbait
- *No captions* in the settings: Youtube turns captions on by itself (a video in another language than your browser), this setting starts every video without them
- works on desktop and mobile, can be added to the home screen of a phone
- instant search in the titles
- keyboard shortcuts: arrows move in the grid, `Space` plays, `n`/`p` play the next/previous video, `l` listens, `Shift`+`↑`/`↓` change channel, `Shift`+`←`/`→` change folder… press `?` for the list
- light: server-rendered pages, [htmx](https://htmx.org/), one CSS file and a few JavaScript modules, no framework, no build step

## 💡 Philosophy

Youtube publishes an RSS feed for every channel. RSS is an open and stable way to share content, it has been for decades: it lets *you* choose how, and with which application, you follow what you like.

*YouRSS+* is a small RSS client for the browser, nothing more:

- **anonymous by default**: a page made of channels in the *URL* can be bookmarked and shared as is; no account is required
- **optional accounts (in progress)**: email magic-link sign-in is being added to keep subscriptions and watched state across devices
- **nothing but the content you chose**: no ads, no suggested videos
- **server state only for accounts (in progress)**: anonymous settings and watched videos stay in your browser; account data will live in SQLite when the account features land
- **straight from Youtube**: thumbnails, avatars and the player are loaded by your browser, the server never relays them

# 📦 Install

## 🐍 From the source

First, you need [uv](https://docs.astral.sh/uv/), see [installation documentation](https://docs.astral.sh/uv/getting-started/installation/):

```sh
$ git clone https://github.com/Ridoc/YouRSS
$ cd YouRSS
$ uv sync
$ uv run --env-file .env -- fastapi dev src/yourss/main.py

# or if you use just
$ just run
```

Then visit [http://localhost:8000/](http://localhost:8000/)

## 🐳 From docker

Using Docker you can run the latest image build on the latest commit:

```sh
$ install -d -o 999 -g 999 data/db data/cache data/backup
$ docker run -d --name yourss \
    -u 999:999 \
    -e YOURSS_DB_PATH=/data/db/yourss.db \
    -e YOURSS_CACHE_FOLDER=/data/cache \
    -v "$PWD/data/db:/data/db" \
    -v "$PWD/data/cache:/data/cache" \
    -v "$PWD/data/backup:/data/backup" \
    -p 127.0.0.1:8000:8000 \
    ghcr.io/ridoc/yourss:main
```

The image runs as UID/GID `999`, keeps SQLite in `/data/db`, and exposes `/healthz`. `scripts/backup-db.sh` writes a consistent backup into `/data/backup`. A compose example is in [`docker-compose.example.yml`](./docker-compose.example.yml).

Then visit [http://localhost:8000/](http://localhost:8000/)

## ☸️ Install Helm Chart for Kubernetes

You need an access to a Kubernetes cluster and `helm` tool installed.
```sh
# from a checkout of this fork
$ helm show values ./charts/yourss > myvalues.yaml
# edit the values for your needs
$ vim myvalues.yaml
# install release
$ helm install my-yourss ./charts/yourss -f myvalues.yaml
```

> Note: you need to add an *Ingress* to access the *Service* from outside your cluster.

You can then forward the service to test it:

```sh
$ kubectl port-forward service/my-yourss 8000:http
```

Then visit [http://localhost:8000/](http://localhost:8000/)

# ⚙️ Configuration

*YouRSS+* can be configured using environment variables.

| VARIABLE | DEFAULT | HELM VALUE | DESCRIPTION |
|----------|---------|------------|-------------|
| YOURSS_CUSTOM_PAGES_ENABLED | `True` | `yourss.customPagesEnabled` | Let visitors build their own page by putting channels in its URL. If set to `false`, the home page and the multi channel pages show an error (`403`), and only the *user* pages remain |
| YOURSS_MAX_PAGE_ITEMS | `12` | `yourss.maxPageItems` | How many channels and playlists the address of a custom page may hold. A longer address is refused (`422`) before anything is asked to Youtube. Configured pages are not limited |
| YOURSS_API_DOCS_ENABLED | `False` | `yourss.apiDocsEnabled` | If set to `true`, the generated API documentation is served at `/docs`, `/redoc` and `/openapi.json`. Meant for development: it is off in the docker image and in the Helm chart |
| YOURSS_USERS_FILE |  | `yourss.users` | The users of the instance and their pages, in a YAML file (the Helm chart builds it from the values and stores it in a *Secret*) |
| YOURSS_CLEAN_TITLES | `False` | `yourss.cleanTitles` | If set to `true`, videos titles are cleaned to prevent UPPERCASE TITLES |
| YOURSS_CACHE_ENABLED | `False` | `yourss.cacheEnabled` | Keep the RSS feeds and the channel metadata (name, avatar) on disk. A feed younger than `YOURSS_CACHE_RSS_TTL` is served without asking Youtube, and when the fetch fails (Youtube's daily transient `404` windows, errors, timeouts) a feed younger than `YOURSS_CACHE_RSS_FALLBACK_TTL` is served instead of the error. Off by default: nothing is cached, every page load fetches its feeds and scrapes a page per channel |
| YOURSS_CACHE_FOLDER |  | `yourss.cacheFolder` | Where the cache lives (created at startup, the application refuses to start if it cannot write there). If unset, a temporary folder `yourss-*` is created at startup |
| YOURSS_CACHE_RSS_TTL | `PT30M` | `yourss.cacheRssTtl` | How long a cached feed is served without asking Youtube (ISO-8601 duration, e.g. `PT30M`, or a number of seconds) |
| YOURSS_CACHE_RSS_FALLBACK_TTL | `PT48H` | `yourss.cacheRssFallbackTtl` | How long a cached feed may replace a failed fetch, at least `YOURSS_CACHE_RSS_TTL`. An older feed is deleted and the error is propagated |
| YOURSS_CACHE_METADATA_TTL | `P7D` | `yourss.cacheMetadataTtl` | How long the name and avatar of a channel are served without scraping its page on Youtube (ISO-8601 duration or seconds). When the scrape fails, the cached ones are served whatever their age. Browsers keep the redirect to an avatar as long |
| YOURSS_LOGIN_TTL | `P90D` | `yourss.loginTtl` | How long a browser stays logged in to an imported legacy password user (ISO-8601 duration or seconds) |
| YOURSS_WARMUP_ENABLED | `False` | `yourss.warmupEnabled` | With the cache on, refresh the feeds of every DB page (but those with `warmup: false`) in the background: a first round at startup, then one every `YOURSS_CACHE_RSS_TTL` minus a minute. Each process refreshes its own cache: run one worker per cache |
| YOURSS_DB_PATH | `data/yourss.db` | `yourss.dbPath` | SQLite database; WAL mode, one writer. The Docker image uses `/data/db/yourss.db` |
| YOURSS_SECRET_KEY | generated | `yourss.secretKey` | Signing key for sessions and CSRF. Empty generates one and persists it in SQLite; set at least 32 characters in production |
| YOURSS_PUBLIC_BASE_URL |  | `yourss.publicBaseUrl` | Absolute HTTPS base for magic links, e.g. `https://yourss.example`. Required when `YOURSS_SMTP_HOST` is set; never derived from the request Host |
| YOURSS_REGISTRATION_ENABLED | `False` | `yourss.registrationEnabled` | Let an unknown email create an account on its first verified magic link. Keep `YOURSS_ADMIN_EMAIL` set on a public instance |
| YOURSS_SESSION_TTL | `P30D` | `yourss.sessionTtl` | Passwordless browser session lifetime (ISO-8601 or seconds) |
| YOURSS_PRIVATE | `False` | `yourss.private` | Gate every HTML/htmx read behind a session, except login, `/healthz`, `/robots.txt` and static assets. API/htmx reads get `401`; HTML gets `/login` |
| YOURSS_NOINDEX | `False` | `yourss.noindex` | Send `X-Robots-Tag: noindex` and `Disallow: /` on `/robots.txt` (also implied by `YOURSS_PRIVATE`) |
| YOURSS_ADMIN_EMAIL |  | `yourss.adminEmail` | Operator admin email; adopted at startup, or the first verified user is promoted when registration is on and this is unset |
| YOURSS_ADMIN_USERNAME |  | `yourss.adminUsername` | Username to adopt for `YOURSS_ADMIN_EMAIL` when importing a legacy users file |
| YOURSS_MAX_PAGES_PER_USER | `50` | `yourss.maxPagesPerUser` | Per-account page cap |
| YOURSS_MAX_PAGE_SUBSCRIPTIONS | `200` | `yourss.maxPageSubscriptions` | Per-page subscription cap after import |
| YOURSS_FORWARDED_ALLOW_IPS | `127.0.0.1` | `yourss.forwardedAllowIps` | Only these direct peers may supply `CF-Connecting-IP` / `X-Forwarded-For`. Set the proxy bridge CIDR, never `*` |
| YOURSS_USERS_FILE |  | `yourss.usersFile` | One-time legacy users.yaml import. Unset skips, leaves the import marker unset, and imports later when set |
| YOURSS_IMPORT_EXPECT_PAGES | `0` | `yourss.importExpectPages` | Fail-closed import floor: the file must have at least this many pages |
| YOURSS_IMPORT_EXPECT_SUBS | `0` | `yourss.importExpectSubs` | Fail-closed import floor: at least this many subscriptions |
| YOURSS_VIDEO_DURATIONS | `False` | `yourss.videoDurations` | Scrape each channel's videos tab during warm-up and cache durations for overview badges |
| YOURSS_DURATION_TTL | `PT6H` | `yourss.durationTtl` | Duration cache lifetime (ISO-8601 or seconds) |
| YOURSS_SMTP_HOST |  | `yourss.smtp.host` | SMTP host for magic links. Unset disables magic-link delivery (the app logs a warning, never the bearer link); configure SMTP for any public instance |
| YOURSS_SMTP_PORT | `587` | `yourss.smtp.port` | SMTP port |
| YOURSS_SMTP_USER |  | `yourss.smtp.username` | SMTP username |
| YOURSS_SMTP_PASSWORD |  | `yourss.smtp.password` | SMTP password (Secret) |
| YOURSS_SMTP_STARTTLS | `True` | `yourss.smtp.starttls` | STARTTLS |
| YOURSS_SMTP_SSL | `False` | `yourss.smtp.ssl` | Implicit TLS |
| YOURSS_SMTP_FROM |  | `yourss.smtp.from` | From address; defaults to the SMTP user |
| YOURSS_TURNSTILE_SITE_KEY |  | `yourss.turnstile.siteKey` | Cloudflare Turnstile site key; must be set with the secret key |
| YOURSS_TURNSTILE_SECRET_KEY |  | `yourss.turnstile.secretKey` | Cloudflare Turnstile secret key (Secret); app-level IP/email limits remain |

> Note: in the users file, a channel is a *channel_id* (24 characters, like `UCa_Dlwrwv3ktrhCy91HpVRw`) or a handle (like `@JonnyGiger`), and a playlist is its id (`PL…`). The address of a page only holds ids.

See [`.env.example`](./.env.example) for the full list.

## 👤 Configure users and pages

A *user* holds *pages*, named lists of channels and playlists, which become its folders:
`http://my-yourss-instance/u/alice/fun`. The sidebar lists the folders, one open at a time, each with its own Home;
`http://my-yourss-instance/u/alice` opens the last folder visited in this browser. Accounts are passwordless: `/login`
emails a one-time link (SMTP), and a verified session owns `/account`, where the user edits the username, pages,
subscriptions and watched state. An imported `users.yaml` account keeps its legacy password until its first
magic-link login claims it; an admin can assign an email to an imported account. A password-protected page still
answers the legacy login form and HTTP Basic, with the signed cookie lasting `YOURSS_LOGIN_TTL`. Changing a password
logs out every browser. Watched videos sync server-side for signed-in users and stay in localStorage for anonymous
visitors.

A page can be *published* under a name, at its own address, easier to type and to remember than an address full
of ids: `http://my-yourss-instance/p/fun` instead of
`http://my-yourss-instance/UCVooVnzQxPSTXTMzSi1s6uw,UCQsmxaMzYr76Yd1iqMEq8TA`. A public page is readable by anyone,
even when its user is behind a password; published names are unique across the instance.

To migrate an existing instance:
- back up the live `users.yaml` and set `YOURSS_USERS_FILE` to a **read-only** copy
- set `YOURSS_IMPORT_EXPECT_PAGES` / `YOURSS_IMPORT_EXPECT_SUBS` to the live counts; startup fails closed on a short file
- the import runs once, then sets `users_imported_at`; later edits to the file do not change the database
- back up `/data/db/yourss.db` with `scripts/backup-db.sh`; restore requires the app stopped
- see [sample `users.yaml`](./samples/users.yaml) for the format

```yaml
users:
  - name: alice              # letters, digits, - and _, 32 characters at most: /u/alice/fun, /u/alice/cards
    pages:
      - name: fun
        publish: fun         # optional, also published at /p/fun
        channels:
          - "@ben-j"
          - "@JonnyGiger"
      - name: cards
        warmup: false        # optional, not refreshed by the warm-up (YOURSS_WARMUP_ENABLED), true by default
        channels:
          - UCQsmxaMzYr76Yd1iqMEq8TA
```

> Migrating from 0.83.0: the top level `pages` are gone, a page now lives in the user listing it, with its `channels`; the
> `label` is gone, the name is what the sidebar shows. A page held by several users is copied into each one; a page that had its own address (no `visible: false`) gets a
> `publish` name in one of them, its former name to keep `/p/<name>` working.

## 🚫 Disable custom pages

By default any visitor can build a page by putting channels in its URL, and the home page explains how.
The administrator of an instance can turn this off, for example to keep the instance for the *user* pages declared in the configuration:

- set `YOURSS_CUSTOM_PAGES_ENABLED=false`, or `yourss.customPagesEnabled: false` in the Helm values
- the home page and the multi channel pages (`/UCVooVnzQxPSTXTMzSi1s6uw,UCQsmxaMzYr76Yd1iqMEq8TA`) then show an error (`403`), and channels can no longer be added
- the configured pages (`/p/<name>`) and users (`/u/<name>`) keep working

> Note: this does not make the instance private, the routes which feed the pages (`/htmx/...`) still answer for any channel.

# 📖 Usage

- the home page `http://yourss.local/` explains how to build your own page: open the demo page, or add your first channel
- you can browse a single channel with: `http://yourss.local/UCVooVnzQxPSTXTMzSi1s6uw`
- you can browse multiple channels and playlists in a single page, their ids separated by commas: `http://yourss.local/UCVooVnzQxPSTXTMzSi1s6uw,UCQsmxaMzYr76Yd1iqMEq8TA`
- an address only holds ids: to add a channel by its handle (`@jonnygiger`) or its address, use the add form, which resolves it
- click a channel in the sidebar of a page to browse its homepage (recent videos, videos, shorts, streams)
- if you defined some *pages*, for example `fun`, the page can be accessed at `http://yourss.local/p/fun`, and a folder of a *user* at `http://yourss.local/u/alice/fun`

> Note: replace `yourss.local` with the URL of your *YouRSS+* instance.

# 🔗 External links

Special thanks to the amazing python frameworks: [FastAPI](https://fastapi.tiangolo.com/), [asyncio](https://docs.python.org/fr/3/library/asyncio.html), [httpx](https://www.python-httpx.org/) and [Pydantic](https://docs.pydantic.dev/) ♥️


- Youtube player API: https://developers.google.com/youtube/player_parameters
