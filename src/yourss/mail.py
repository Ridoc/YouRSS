"""Magic-link delivery: SMTP in production, a log sink in development."""

from __future__ import annotations

from email.message import EmailMessage
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

import aiosmtplib
from loguru import logger

if TYPE_CHECKING:
    from .schema import AppSettings


class Mailer(Protocol):
    async def send(self, to: str, subject: str, text: str) -> None: ...


class LogMailer:
    """Development fallback: never logs the bearer link; configure SMTP to deliver it."""

    async def send(self, to: str, subject: str, text: str) -> None:
        logger.warning("Magic link generated for {} but YOURSS_SMTP_HOST is unset", to)


class SmtpMailer:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    async def send(self, to: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.smtp_from or self.settings.smtp_user or "yourss@localhost"
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        await aiosmtplib.send(
            message,
            hostname=self.settings.smtp_host,
            port=self.settings.smtp_port,
            username=self.settings.smtp_user,
            password=self.settings.smtp_password.get_secret_value() if self.settings.smtp_password else None,
            start_tls=self.settings.smtp_starttls,
            use_tls=self.settings.smtp_ssl,
            timeout=15,
        )


def build_magic_link(public_base_url: str | None, token: str) -> str:
    if not public_base_url:
        raise RuntimeError("YOURSS_PUBLIC_BASE_URL is required to build magic links")
    return f"{public_base_url.rstrip('/')}/auth/verify?token={quote(token)}"


def get_mailer(settings: AppSettings) -> Mailer:
    if settings.smtp_host:
        if not settings.public_base_url:
            raise RuntimeError("YOURSS_PUBLIC_BASE_URL is required when YOURSS_SMTP_HOST is set")
        return SmtpMailer(settings)
    return LogMailer()
