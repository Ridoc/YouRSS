from datetime import timedelta
from pathlib import Path

from pydantic import ValidationError
from pytest import MonkeyPatch, raises

from yourss.schema import AppSettings


def test_account_ops_and_import_settings_parse(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "yourss-plus.db"
    users_file = tmp_path / "users.yaml"
    values = {
        "YOURSS_DB_PATH": str(db_path),
        "YOURSS_SECRET_KEY": "configured-secret",
        "YOURSS_PUBLIC_BASE_URL": "https://yourss.example",
        "YOURSS_SESSION_TTL": "P7D",
        "YOURSS_REGISTRATION_ENABLED": "true",
        "YOURSS_PRIVATE": "true",
        "YOURSS_NOINDEX": "true",
        "YOURSS_SMTP_HOST": "smtp.example.com",
        "YOURSS_SMTP_PORT": "465",
        "YOURSS_SMTP_USER": "mailer",
        "YOURSS_SMTP_PASSWORD": "smtp-secret",
        "YOURSS_SMTP_STARTTLS": "false",
        "YOURSS_SMTP_SSL": "true",
        "YOURSS_SMTP_FROM": "YouRSS+ <noreply@example.com>",
        "YOURSS_TURNSTILE_SITE_KEY": "site-key",
        "YOURSS_TURNSTILE_SECRET_KEY": "turnstile-secret",
        "YOURSS_ADMIN_EMAIL": "admin@example.com",
        "YOURSS_ADMIN_USERNAME": "zeno",
        "YOURSS_MAX_PAGES_PER_USER": "12",
        "YOURSS_MAX_PAGE_SUBSCRIPTIONS": "300",
        "YOURSS_FORWARDED_ALLOW_IPS": "172.16.0.0/12",
        "YOURSS_VIDEO_DURATIONS": "true",
        "YOURSS_DURATION_TTL": "PT2H",
        "YOURSS_USERS_FILE": str(users_file),
        "YOURSS_IMPORT_EXPECT_PAGES": "12",
        "YOURSS_IMPORT_EXPECT_SUBS": "149",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    config = AppSettings()

    assert config.db_path == db_path
    assert config.secret_key is not None and config.secret_key.get_secret_value() == "configured-secret"
    assert config.public_base_url == "https://yourss.example"
    assert config.session_ttl == timedelta(days=7)
    assert config.registration_enabled is True
    assert config.private is True
    assert config.noindex is True
    assert config.smtp_host == "smtp.example.com"
    assert config.smtp_port == 465
    assert config.smtp_user == "mailer"
    assert config.smtp_password is not None and config.smtp_password.get_secret_value() == "smtp-secret"
    assert config.smtp_starttls is False
    assert config.smtp_ssl is True
    assert config.smtp_from == "YouRSS+ <noreply@example.com>"
    assert config.turnstile_site_key == "site-key"
    assert config.turnstile_secret_key is not None
    assert config.turnstile_secret_key.get_secret_value() == "turnstile-secret"
    assert str(config.admin_email) == "admin@example.com"
    assert config.admin_username == "zeno"
    assert config.max_pages_per_user == 12
    assert config.max_page_subscriptions == 300
    assert config.forwarded_allow_ips == "172.16.0.0/12"
    assert config.video_durations is True
    assert config.duration_ttl == timedelta(hours=2)
    assert config.users_file == users_file
    assert config.import_expect_pages == 12
    assert config.import_expect_subs == 149


def test_bad_session_ttl_is_rejected(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("YOURSS_SESSION_TTL", "-1")
    with raises(ValidationError):
        AppSettings()


def test_unparseable_session_ttl_is_rejected(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("YOURSS_SESSION_TTL", "not-a-duration")
    with raises(ValidationError):
        AppSettings()
