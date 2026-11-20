# YouRSS fork — accounts, self-service subscriptions, issue fixes
---
title: "YouRSS fork design"
date: "2026-11-20"
status: "REVIEW — awaiting user approval"
fork: "Ridoc/YouRSS"
upstream: "essembeh/YouRSS"
license: "Apache-2.0 (upstream retained)"
target: "public instance at yourss.occupysaas.net"
---

META:
  GOAL: "Fork upstream, add passwordless accounts + self-service subscriptions + cross-device watched state, fix issues #4-#17, deploy publicly."
  NON_GOALS: ["no frontend framework", "no video/thumbnail proxying", "no server-side YouTube session", "no local like state", "no reimplementation of upstream UI"]
  PRESERVED: ["RSS-first", "pass-through media", "server-rendered Jinja + htmx", "vanilla JS/CSS", "upstream tests", "Apache-2.0 attribution"]

DECISIONS:  # user-approved 2026-11-20
  FORK: "public Ridoc/YouRSS, CI image ghcr.io/ridoc/yourss"
  APPROACH: "extend upstream in place; do not rewrite"
  USERS: "open registration on yourss.occupysaas.net; accounts optional"
  ANONYMOUS: "URL-only pages stay available exactly as today"
  IDENTITY: "email magic link, no password; unique editable username slug for /u/<name>"
  STORAGE: "SQLite in the app, WAL, one uvicorn worker"
  WATCHED: "localStorage for anonymous; server-side per-account sync for logged-in users"
  LIKES: "no local like state; keep YouTube embed controls; add explicit Like on YouTube link"
  CAPTCHA: "Cloudflare Turnstile when configured; app rate limits as fallback"
  EMAIL: "SMTP only; dev mode logs magic links"
  OLD_DOMAIN: "decommission yourss.5circles.eu after migration; 301 window then remove"
  URLS: "editable username slug; preserve /u/zeno and existing folders"
  ADMIN: "bootstrap operator account from YOURSS_ADMIN_EMAIL; role=admin"

ARCHITECTURE:
  FLOW: "Browser -> Cloudflare/Caddy -> nginx -> FastAPI -> SQLite + disk cache + SMTP"
  PACKAGE: "keep src/yourss layout; add focused modules; no new frontend build"
  NEW_MODULES:
    db.py: "SQLite connect, PRAGMAs, migrations, seed/import"
    accounts.py: "magic-link tokens, sessions, CSRF, users, roles, permissions"
    mail.py: "SMTP sender interface; dev log sender"
    captcha.py: "Turnstile siteverify client; no-op when unconfigured"
    subscriptions.py: "page/subscription repository, validation, resolution, reorder"
    watched.py: "server-side watched map and merge"
    routers/auth.py: "/login, /auth/request, /auth/verify, /logout"
    routers/account.py: "/account, /account/pages, /account/watched, export/delete"
    routers/admin.py: "/admin/users, /admin/status"
    middleware/security.py: "CSRF, noindex, private-mode gate helpers"
  NEW_DEPENDENCIES:
    aiosqlite: "async SQLite access"
    aiosmtplib: "SMTP delivery"
    email-validator: "Pydantic EmailStr"
    note: "no ORM, no auth framework, no frontend dependency"
  EXISTING_TOUCHED:
    schema.py: "AppSettings additions"
    security.py: "legacy password bridge only; new sessions live in accounts.py"
    settings.py: "DB init, cache init, secret bootstrap"
    main.py: "db lifespan, middleware, routers, healthz/robots/404"
    warmup.py: "DB-backed pages, live/stale/failed counts, duration refresh"
    routers/web.py: "user pages from DB; keep anonymous URL pages"
    routers/htmx.py: "private-mode gate; no other behavior change"
    templates: "login, account, admin, empty states, durations, CSRF fields"
    static/yourss-watched.js: "server map when logged in; localStorage otherwise"
  INVARIANTS:
    - "Anonymous URL page behavior must not regress."
    - "No secrets in git, docs, logs, or chat."
    - "All mutating requests require CSRF."
    - "All user data access is scoped by user_id unless admin."
    - "One uvicorn worker in examples; SQLite writes serialize."

DATA_MODEL:
  CONNECTION:
    path: "YOURSS_DB_PATH (default yourss.db; Docker /data/yourss.db)"
    pragmas: ["journal_mode=WAL", "foreign_keys=ON", "synchronous=NORMAL", "busy_timeout=5000"]
    mode: "0600 after creation"
    migrations: "PRAGMA user_version + ordered migrations in db.py; no Alembic"
  TABLES:
    users:
      columns:
        id: "INTEGER PRIMARY KEY"
        email: "TEXT NULL UNIQUE COLLATE NOCASE"
        username: "TEXT NOT NULL UNIQUE COLLATE NOCASE"
        display_name: "TEXT NOT NULL DEFAULT ''"
        role: "TEXT NOT NULL CHECK(role IN ('admin','user')) DEFAULT 'user'"
        created_at: "TEXT NOT NULL"
        disabled_at: "TEXT NULL"
        session_version: "INTEGER NOT NULL DEFAULT 1"
        legacy_password_hash: "TEXT NULL"
        legacy_password_method: "TEXT NULL"
        watched_imported_at: "TEXT NULL"
      indexes: ["users(email)", "users(username)"]
      invariant: "email not null for magic-link accounts; imported YAML users may be legacy-only until an admin sets an email"
    pages:
      columns:
        id: "INTEGER PRIMARY KEY"
        user_id: "INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE"
        name: "TEXT NOT NULL"
        publish: "TEXT NULL UNIQUE COLLATE NOCASE"
        warmup: "INTEGER NOT NULL DEFAULT 1"
        position: "INTEGER NOT NULL DEFAULT 0"
        created_at: "TEXT NOT NULL"
        updated_at: "TEXT NOT NULL"
      constraints: ["UNIQUE(user_id, name)", "publish unique globally when not null"]
    subscriptions:
      columns:
        id: "INTEGER PRIMARY KEY"
        page_id: "INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE"
        ref: "TEXT NOT NULL"
        kind: "TEXT NOT NULL CHECK(kind IN ('channel','playlist'))"
        canonical_id: "TEXT NOT NULL"
        position: "INTEGER NOT NULL DEFAULT 0"
        created_at: "TEXT NOT NULL"
      constraints: ["UNIQUE(page_id, canonical_id)"]
      indexes: ["subscriptions(page_id, position)"]
    watched:
      columns:
        user_id: "INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE"
        video_id: "TEXT NOT NULL"
        watched_at: "INTEGER NOT NULL"
      constraints: ["PRIMARY KEY(user_id, video_id)"]
      cap: "keep newest 3000 per user; delete oldest on insert"
    login_tokens:
      columns:
        id: "INTEGER PRIMARY KEY"
        email: "TEXT NOT NULL COLLATE NOCASE"
        token_hash: "TEXT NOT NULL UNIQUE"
        purpose: "TEXT NOT NULL CHECK(purpose IN ('login'))"
        expires_at: "INTEGER NOT NULL"
        used_at: "INTEGER NULL"
        created_at: "INTEGER NOT NULL"
        ip_hash: "TEXT NULL"
      indexes: ["login_tokens(email, created_at)"]
    settings:
      columns: ["key TEXT PRIMARY KEY", "value TEXT NOT NULL"]
      use: "secret_key fallback, schema metadata"
    audit_log:
      columns:
        id: "INTEGER PRIMARY KEY"
        user_id: "INTEGER NULL"
        action: "TEXT NOT NULL"
        detail: "TEXT NOT NULL DEFAULT ''"
        created_at: "TEXT NOT NULL"
        ip_hash: "TEXT NULL"
    video_durations:
      columns:
        video_id: "TEXT PRIMARY KEY"
        seconds: "INTEGER NOT NULL"
        source: "TEXT NOT NULL"
        fetched_at: "INTEGER NOT NULL"
      use: "RSS overview enrichment; rebuildable cache"
    warmup_status:
      columns:
        channel: "TEXT PRIMARY KEY"
        state: "TEXT NOT NULL"
        last_ok_at: "INTEGER NULL"
        last_error: "TEXT NULL"
        checked_at: "INTEGER NOT NULL"
      use: "per-channel last-fetch status for admin/status"

CONFIG:  # every YOURSS_* mirrored in README table + Helm values
  CORE:
    YOURSS_DB_PATH: "SQLite file; default yourss.db"
    YOURSS_SECRET_KEY: "session/CSRF signing key; env preferred; if absent generate once into settings table and warn"
    YOURSS_PUBLIC_BASE_URL: "absolute base for magic links; required in production"
    YOURSS_SESSION_TTL: "PT30D default"
  ACCOUNTS:
    YOURSS_REGISTRATION_ENABLED: "false default; true on public instance"
    YOURSS_ADMIN_EMAIL: "bootstrap admin email; no default"
    YOURSS_ADMIN_USERNAME: "bootstrap username; default zeno when email set"
    YOURSS_MAX_PAGES_PER_USER: "50 default"
    YOURSS_MAX_PAGE_ITEMS: "existing setting for custom URL pages; unchanged"
    YOURSS_MAX_PAGE_SUBSCRIPTIONS: "200 default; imported pages may keep more existing entries but new additions are refused above the cap"
  PRIVATE:
    YOURSS_PRIVATE: "false default; true gates every HTML/htmx route behind a session"
    YOURSS_NOINDEX: "false default; true emits robots Disallow and X-Robots-Tag"
  EMAIL:
    YOURSS_SMTP_HOST: "SMTP host; unset = log magic links (dev only)"
    YOURSS_SMTP_PORT: "587 default"
    YOURSS_SMTP_USER: "SMTP username"
    YOURSS_SMTP_PASSWORD: "SMTP password"
    YOURSS_SMTP_STARTTLS: "true default"
    YOURSS_SMTP_SSL: "false default; true for implicit TLS (port 465)"
    YOURSS_SMTP_FROM: "From address, e.g. YouRSS <noreply@occupysaas.net>"
  CAPTCHA:
    YOURSS_TURNSTILE_SITE_KEY: "public site key"
    YOURSS_TURNSTILE_SECRET_KEY: "secret key; unset = CAPTCHA disabled"
    behavior: "when configured, verification is fail-closed; when unset, rate limits only"
  OPS:
    YOURSS_FORWARDED_ALLOW_IPS: "127.0.0.1 default; passed to uvicorn"
    YOURSS_VIDEO_DURATIONS: "false default; true enables #17 enrichment"
    YOURSS_DURATION_TTL: "PT6H default"
  IMPORT:
    YOURSS_USERS_FILE: "one-time import source when users table empty; archived afterward"

AUTH:
  LOGIN_FLOW:
    - "GET /login sets a pre-session CSRF cookie and shows email form + Turnstile when configured"
    - "POST /auth/request validates CSRF, normalizes email; rate-limit IP+email; always same response"
    - "create 32-byte token; store SHA-256 hash; TTL 15 min; purpose=login"
    - "send https://<base>/auth/verify?token=... via SMTP; dev logs link"
    - "GET /auth/verify renders a confirmation page without consuming the token (email scanners must not burn it)"
    - "POST /auth/verify consumes token atomically; creates account if open registration"
    - "set yourss-session cookie; redirect to safe next path or /"
  SESSION:
    cookie: "yourss-session; HttpOnly; Secure when https; SameSite=Lax; path=/"
    payload: "user_id.expiry.session_version.hmac"
    key: "YOURSS_SECRET_KEY or settings.secret_key"
    invalidation: "bump users.session_version; logout-all does this"
    legacy: "existing yourss-login password cookie still accepted for imported accounts until first magic-link login"
  CSRF:
    cookie: "yourss-csrf; non-HttpOnly; SameSite=Lax"
    forms: "hidden csrf_token"
    htmx: "X-CSRF-Token header from meta tag"
    pre_session: "GET /login issues a CSRF cookie/token so POST /auth/request is protected too"
    validation: "constant-time compare + HMAC; 403 on missing/invalid"
  CLIENT_IP:
    default: "request.client.host"
    proxy: "CF-Connecting-IP or X-Forwarded-For only when the direct peer is in YOURSS_FORWARDED_ALLOW_IPS"
    use: "rate limits and audit ip_hash only; never trust a header from an untrusted peer"
  PERMISSIONS:
    anonymous: "URL pages, public /p/<name>, static, healthz, robots, login"
    user: "own /account, own pages/subscriptions, own watched; read public pages"
    admin: "all users, all pages, disable/promote/delete, send magic link"
    private_mode: "all HTML/htmx routes require session; allowlist login/static/healthz/robots/favicon"
    page_visibility: "pages are publicly readable by default; editing requires owner/admin; YOURSS_PRIVATE gates all reads"
  BOOTSTRAP:
    - "if users empty and YOURSS_ADMIN_EMAIL set: create admin with username YOURSS_ADMIN_USERNAME or email local part"
    - "if users empty and registration enabled and no admin email: first verified login becomes admin (dev convenience; warn)"
    - "if imported users.yaml exists: import first; assign YOURSS_ADMIN_EMAIL to username YOURSS_ADMIN_USERNAME (default zeno) and role=admin, else create admin"
  LEGACY_BRIDGE:
    - "import argon2/clear hashes into legacy_password_hash; imported users have email NULL until claimed"
    - "POST /u/<user> password and HTTP Basic still work for imported account"
    - "admin may assign an email to an imported account; then magic link works"
    - "on first successful magic-link login: clear legacy hash; password login stops"
  RATE_LIMITS:
    login_request: "5/min per IP, 3/hour per email"
    verify: "20/min per IP"
    password_legacy: "5/min per IP+username; 429 + Retry-After"
    general_auth: "60/min per IP"
    implementation: "in-process limiter (one worker); nginx/Cloudflare layers remain"
  ARGON2_DOS:
    semaphore: "asyncio.Semaphore(2) around verify_password"
    scope: "legacy password + Basic only"
    behavior: "queue or 429; never unbounded"

ACCOUNTS_UI:
  ROUTES:
    /login: "email form"
    /auth/request: "POST send link"
    /auth/verify: "GET confirmation page; POST consumes link and sets session"
    /logout: "POST CSRF; clear session"
    /account: "profile, username edit, display name, logout all, export, delete"
    /account/pages: "page list/create/rename/delete/reorder"
    /account/pages/<name>: "subscription editor"
    /account/watched: "watched count, import button, clear"
    /admin/users: "user list, disable/enable, promote, send link, delete"
    /admin/status: "warm-up status, live/cached/failed, last errors"
  PAGE_EDITOR:
    add: "POST ref; resolve handle/URL/video/playlist; store ref + canonical_id; reject duplicate"
    remove: "POST delete; confirm"
    reorder: "POST ordered ids; transaction; positions 0..n"
    publish: "optional unique name; regex ^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"
    warmup: "toggle per page"
    limits: "YOURSS_MAX_PAGE_SUBSCRIPTIONS and YOURSS_MAX_PAGES_PER_USER; custom URL pages keep YOURSS_MAX_PAGE_ITEMS"
  VALIDATION:
    refs: "is_channel_id/is_playlist_id/is_user or resolve via YoutubeApi; reject unknown"
    read_path: "catch ValueError from cache_path; render failed subscription; never 500"
    names: "same regex as upstream; duplicate checks in DB + friendly error"
  DELETION:
    account: "hard delete cascade; typed confirmation"
    page: "hard delete cascade; typed confirmation"
    export: "JSON download: account, pages, subscriptions, watched"

WATCHED_SYNC:
  ANONYMOUS: "unchanged localStorage yourss-watched map"
  LOGGED_IN:
    render: "server embeds current map in a JSON script tag; yourss-watched.js prefers it"
    write: "POST /api/watched {add:[id], remove:[id]} with CSRF; server timestamps"
    read: "GET /api/watched returns map; used on page load / after login"
    first_login: "button imports current browser map; sets users.watched_imported_at"
    authority: "server wins after import; other devices converge on next page load"
    cap: "3000 newest per user"
    counters: "All/New filter and per-channel badges read the same map"
  NO_REALTIME: "no SSE/websocket in v1; document next-navigation sync"

ISSUES:
  "#4": "Turnstile on /login and /auth/request when configured; in-process rate limits fallback; 429"
  "#5": "YOURSS_PRIVATE gates all HTML and htmx routes; login/static/healthz/robots/favicon allowlisted"
  "#6": "legacy Argon2 throttled per IP+username and Basic attempts; semaphore(2); 429 + Retry-After"
  "#7": "YOURSS_SECRET_KEY independent of password hashes; rotation invalidates sessions; no hash-derived HMAC"
  "#8": "validate subscriptions on write; read path catches ValueError and returns failed status; no 500"
  "#9": "htmx routes gated in private mode; public instance remains open by design"
  "#10": "CSRF on logout and every mutation; missing token -> 403"
  "#11": "README + docker-compose.example.yml document UID/GID 999, volume ownership, user: directive; no root entrypoint"
  "#12": "/healthz checks app + SQLite SELECT 1 + cache writable; Docker HEALTHCHECK uses it"
  "#13": "catch-all 404 for unknown paths; /favicon.ico; 403 only when custom pages disabled; body duplication with #14 noted"
  "#14": "warm-up counts live/cached/failed; one aggregate warning; warmup_status table; /admin/status"
  "#15": "/robots.txt route; X-Robots-Tag noindex for /u/*, /login, /account, /admin when YOURSS_NOINDEX or private"
  "#16": "YOURSS_FORWARDED_ALLOW_IPS default 127.0.0.1; Docker CMD passes it; docs say bind 127.0.0.1 or trust proxy"
  "#17": "duration_seconds on VideoDescription; parse lengthText/lockup overlays; video_durations cache; warm-up refresh; no badge when unknown"

DURATIONS:
  SOURCE: "channel /videos scrape returns duration for many videos; playlist scrape too"
  RSS_OVERVIEW: "enrich by video_id lookup in video_durations; missing = no badge"
  WARMUP: "when YOURSS_VIDEO_DURATIONS, refresh each channel's durations on YOURSS_DURATION_TTL"
  COST: "one channel page per TTL, not one video page per card; failures degrade silently"
  PARSERS: "legacy videoRenderer.lengthText.simpleText; lockup thumbnailOverlayTimeStatusViewModel.text; parse H:MM:SS/MM:SS"

TEMPLATES_JS:
  NEW_TEMPLATES: ["pages/login.jinja-html rewrite", "pages/account.jinja-html", "partials/account-pages.jinja-html", "partials/subscription-editor.jinja-html", "pages/admin-users.jinja-html", "pages/admin-status.jinja-html"]
  TOUCHED: ["pages/base.jinja-html (account link, CSRF meta, watched JSON)", "partials/videos.jinja-html (duration badge, like link)", "partials/settings.jinja-html (account/logout)", "partials/sidebar.jinja-html (edit affordance for owner)"]
  JS: ["yourss-watched.js server map", "yourss.js account actions", "yourss-core.js csrf helper"]
  CSS: "yourss- prefixed classes; reuse existing tokens/themes; no build step"

OPS:
  DOCKER:
    base: "python:3.13-slim; non-root app 999:999; tini"
    volumes: ["/data/db", "/data/cache"]
    healthcheck: "python -c urllib.request /healthz; interval 60s"
    healthz: "DB SELECT 1 always; cache writable only when cache_enabled"
    cmd: "uvicorn --forwarded-allow-ips ${YOURSS_FORWARDED_ALLOW_IPS:-127.0.0.1}"
    compose: "docker-compose.example.yml with user: 999:999, volumes, env, no published app port"
  BACKUP:
    db: "sqlite3 /data/yourss.db '.backup /backup/yourss-<ts>.db' or VACUUM INTO"
    cache: "rebuildable; not backed up"
    docs: "README backup/restore + restic note"
  HELM:
    values: "one value per new setting; persistence for SQLite; secret for SMTP/Turnstile/secret key; keep upstream defaults"
  CI:
    keep: ["python-check", "python-test", "python-audit"]
    change: ["docker-build image ghcr.io/ridoc/yourss", "tags main/latest/<tag>"]
    remove: ["demo-deploy Render", "helm-release upstream Pages"]
    add: ["docker smoke + /healthz"]
  DEPLOY:
    domain: "yourss.occupysaas.net"
    edge: "Cloudflare proxied -> Caddy -> nginx -> app"
    tls: "Full (strict)"
    smtp_dns: "SPF/DKIM/DMARC for magic links"
    turnstile: "site + secret keys in env"
    migration: "copy users.yaml, first boot imports, verify 12 folders/73 channels, then archive"
    old_domain: "301 to new for 30 days, then remove DNS/stack after backup"
  SECRETS:
    git: "never commit .env, SMTP/Turnstile secrets, DB, users.yaml"
    db: "0600; backup encrypted/restricted"
    logs: "never log tokens or raw emails beyond hashed/IP"

PHASES:
  P0: "fork, baseline just check/test, CI image builds"
  P1a: "SQLite + migrations + magic links + SMTP + Turnstile + sessions + CSRF + admin bootstrap + users.yaml import + legacy bridge"
  P1b: "/account + pages CRUD + subscriptions + publish + admin users + permissions + watched sync"
  P2: "issues #4-#16 security/robustness/ops + private mode + docs"
  P3: "#17 durations + Lighthouse/a11y pass + README/Helm parity + release"
  P4: "deploy public instance, import, verify, 301 old domain, decommission"

TESTS:
  GATES: ["just check", "just test"]
  UNIT: ["db migrations idempotent", "magic token hash/single-use/expiry", "GET verify does not consume; POST does", "session signature/version", "CSRF reject", "subscription validation", "watched cap/merge", "private allowlist", "robots/healthz/404", "healthz with cache disabled"]
  INTEGRATION: ["register -> magic link -> session", "create page -> add channel -> reload", "two sessions watched sync", "import users.yaml -> 12 folders/73 channels", "admin disable/promote/delete", "legacy password still works then clears"]
  FAKES: ["SMTP capture", "Turnstile bypass in tests", "existing YouTube fake transport"]
  BROWSER: ["anonymous URL page", "login", "page editor", "mobile viewport", "embedded player like control/link"]

ACCEPTANCE:
  - "Fresh DB: register by magic link, create page, add channel, reload, watched syncs to second session."
  - "Imported DB: zeno keeps 12 folders/73 channels; /u/zeno/<folder> works."
  - "Private mode: all HTML/htmx routes require login except allowlist."
  - "/healthz 200; unknown path 404; /robots.txt correct; logout without CSRF 403; throttled login 429."
  - "Docker image non-root; healthcheck passes; SQLite survives restart; backup script works."
  - "Public domain live; old domain 301 then off; no secrets in git."

RISKS:
  upstream_drift: "modular new files; merge upstream/main before P1 and monthly; no core rewrite"
  smtp_deliverability: "SPF/DKIM/DMARC; dev log mode; test send before launch"
  signup_abuse: "Turnstile + IP/email throttles + nginx/Cloudflare; admin disable/delete"
  sqlite_lock: "WAL, short transactions, one worker; .backup script"
  scraping_cost: "duration cache + channel-page granularity + TTL; feature off = no scrape"
  likes: "no cross-origin control; keep embed; explicit Like on YouTube link"
  migration: "import into temp DB, verify counts, keep users.yaml backup"
  privacy: "watched history is server-side for logged-in users; document; export/delete"

OPEN_QUESTIONS:  # none blocking; user-supplied at deploy
  - "SMTP credentials and sender domain DNS"
  - "Turnstile site/secret keys"
  - "Admin email (set in deployment env; not committed)"
  - "Old-domain 301 window length (default 30 days)"

SELF_REVIEW:
  placeholders: "none"
  contradictions_resolved:
    - "imported folders may exceed the new subscription cap; existing rows kept, new additions refused"
    - "imported YAML users have no email; email stays NULL until an admin claims the account"
    - "magic-link GET only shows confirmation; POST consumes, so email scanners cannot burn tokens"
  ambiguity_resolved:
    - "pages are publicly readable by default; YOURSS_PRIVATE gates all reads"
    - "one uvicorn worker; SQLite writes serialize"
    - "when Turnstile is configured, verification is fail-closed; unconfigured = rate limits only"
  scope_check: "issues #4-#17 + accounts + migration + deployment; no local likes, no PUID entrypoint, no realtime watched sync"
