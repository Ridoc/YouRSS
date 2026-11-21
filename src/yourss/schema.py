from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Every setting is a YOURSS_<NAME> environment variable (see the README and the Helm values)."""

    # WHY: compose/Helm often render optional settings as empty strings; EmailStr would reject "".
    model_config = SettingsConfigDict(env_prefix="YOURSS_", env_ignore_empty=True)

    # Let visitors build their own multi channel page; off, only user and channel pages remain
    custom_pages_enabled: bool = True
    # How many channels and playlists the address of a custom page may hold
    max_page_items: int = Field(default=12, ge=1)
    # Serve the generated API documentation (/docs, /redoc, /openapi.json); off outside development
    api_docs_enabled: bool = False
    # SQLite file; its parent is created at startup, the file plus WAL/SHM are 0600
    db_path: Path = Path("yourss.db")
    # HMAC signing key; env wins, otherwise generated once into the settings table
    secret_key: SecretStr | None = None
    # Absolute base for magic links; never derived from the request Host header
    public_base_url: str | None = None
    # Browser session lifetime
    session_ttl: timedelta = timedelta(days=30)
    # Allow unknown emails to create an account on first verified magic link
    registration_enabled: bool = False
    # Gate every HTML/htmx read behind a session
    private: bool = False
    # Emit robots Disallow and X-Robots-Tag
    noindex: bool = False
    # SMTP delivery; unset host logs magic links (development only)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_from: str | None = None
    # Cloudflare Turnstile; unset secret disables the CAPTCHA
    turnstile_site_key: str | None = None
    turnstile_secret_key: SecretStr | None = None
    # Operator bootstrap account
    admin_email: EmailStr | None = None
    admin_username: str | None = None
    # Per-user limits; imported pages may exceed the subscription cap once
    max_pages_per_user: int = Field(default=50, ge=1)
    max_page_subscriptions: int = Field(default=200, ge=1)
    # Only these direct peers may supply CF-Connecting-IP / X-Forwarded-For
    forwarded_allow_ips: str = "127.0.0.1"
    # #17: fetch and cache video durations during warm-up
    video_durations: bool = False
    duration_ttl: timedelta = timedelta(hours=6)
    # YAML file declaring the user pages; one-time import source
    users_file: Path | None = None
    # Import safety floor (0 disables the check)
    import_expect_pages: int = Field(default=0, ge=0)
    import_expect_subs: int = Field(default=0, ge=0)
    # Capitalize the titles to tame UPPERCASE ones
    clean_titles: bool = False
    # Disk cache of the feeds and channel metadata (docs/specs/disk-cache.md), off by default; a temporary folder when
    # none is given
    cache_enabled: bool = False
    cache_folder: Path | None = None
    # A cached feed younger than the TTL is served without asking Youtube
    cache_rss_ttl: timedelta = timedelta(minutes=30)
    # When the fetch fails, a cached feed younger than this is served instead of the error
    cache_rss_fallback_ttl: timedelta = timedelta(hours=48)
    # A cached channel description younger than this is served without scraping Youtube
    cache_metadata_ttl: timedelta = timedelta(days=7)
    # Refresh the feeds of every page in the background, a round every cache_rss_ttl minus a minute
    warmup_enabled: bool = False
    # How long the login of a user behind a password lasts in a browser (docs/specs/login.md §8)
    login_ttl: timedelta = timedelta(days=90)

    @model_validator(mode="after")
    def _check_ttls(self) -> Self:
        if self.cache_rss_ttl > self.cache_rss_fallback_ttl:
            raise ValueError("cache_rss_ttl must not exceed cache_rss_fallback_ttl")
        if self.session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be positive")
        if self.duration_ttl <= timedelta(0):
            raise ValueError("duration_ttl must be positive")
        return self


class PasswordMethod(Enum):
    CLEAR = "clear"
    ARGON2 = "argon2"


class Password(BaseModel):
    method: PasswordMethod
    value: SecretStr


# Channel ids, handles and playlist ids, at least one
Channels = Annotated[list[str], Field(min_length=1)]
# A page or user name: in the URL (`/p/<page>`, `/u/<user>/<page>`), a DOM id, a Secret key in the chart
Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")]


class Page(BaseModel):
    """A page of a user, its folder `/u/<user>/<name>`; with `publish`, it is also served at `/p/<publish>`."""

    # Unknown keys are refused: a typo on `publish` must not leave a page unpublished, or the reverse
    model_config = ConfigDict(extra="forbid")

    name: Name
    # The name of the page at `/p/<publish>`, unique across the users
    publish: Name | None = None
    # Refreshed by the background warm-up (YOURSS_WARMUP_ENABLED); off, its feeds are only fetched on a visit
    warmup: bool = True
    channels: Channels


class User(BaseModel):
    """A user holds pages, its folders (`/u/<name>/<page>`); its password protects them all, not their public copy."""

    model_config = ConfigDict(extra="forbid")

    name: Name
    password: Password | None = None
    pages: Annotated[list[Page], Field(min_length=1)]

    @model_validator(mode="after")
    def _check_pages(self) -> Self:
        names = [page.name for page in self.pages]
        if len(set(names)) != len(names):
            raise ValueError("the pages of a user have distinct names")
        return self

    def page(self, name: str) -> Page | None:
        return next((page for page in self.pages if page.name == name), None)


class UsersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[User] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_names(self) -> Self:
        users = [user.name for user in self.users]
        if len(set(users)) != len(users):
            raise ValueError("the users have distinct names")
        published = [page.publish for page in self.pages if page.publish is not None]
        if len(set(published)) != len(published):
            raise ValueError("the published pages have distinct names")
        return self

    @property
    def pages(self) -> list[Page]:
        """Every page, user after user."""
        return [page for user in self.users for page in user.pages]

    def public_page(self, name: str) -> Page | None:
        return next((page for page in self.pages if page.publish == name), None)
