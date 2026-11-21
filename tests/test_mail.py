"""Magic-link mail: link built from the configured public base, SMTP needs it (C2.I1)."""

from pytest import raises

from yourss import mail
from yourss.settings import current_config


def test_magic_link_uses_the_public_base_url() -> None:
    assert mail.build_magic_link("https://rss.example", "abc123") == "https://rss.example/auth/verify?token=abc123"


def test_magic_link_does_not_double_the_slash() -> None:
    assert mail.build_magic_link("https://rss.example/", "abc123") == "https://rss.example/auth/verify?token=abc123"


def test_magic_link_requires_a_public_base_url() -> None:
    with raises((ValueError, RuntimeError)):
        mail.build_magic_link("", "abc123")


def test_log_mailer_when_smtp_is_unset() -> None:
    config = current_config.model_copy(update={"smtp_host": None})
    assert isinstance(mail.get_mailer(config), mail.LogMailer)


def test_smtp_requires_the_public_base_url() -> None:
    # A magic link built from the request Host would be a phishing door
    config = current_config.model_copy(update={"smtp_host": "smtp.example", "public_base_url": None})
    with raises((ValueError, RuntimeError)):
        mail.get_mailer(config)


def test_smtp_mailer_when_smtp_is_configured() -> None:
    config = current_config.model_copy(
        update={
            "smtp_host": "smtp.example",
            "smtp_from": "YouRSS <noreply@example.com>",
            "public_base_url": "https://rss.example",
        }
    )
    assert isinstance(mail.get_mailer(config), mail.SmtpMailer)
