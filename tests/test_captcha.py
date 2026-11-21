"""Turnstile verification: success, invalid, timeout and 5xx must fail closed (C2.I1)."""

from collections.abc import Awaitable, Callable

import httpx

from yourss import captcha
from yourss.captcha import TURNSTILE_URL, verify_turnstile

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


def mock_client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_turnstile_accepts_a_valid_response() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"success": True, "error-codes": []})

    async with mock_client(handler) as client:
        assert await verify_turnstile("test-secret", "turnstile-response", "203.0.113.7", client=client) is True
    assert len(seen) == 1
    assert str(seen[0].url) == TURNSTILE_URL
    body = seen[0].content.decode()
    assert "test-secret" in body
    assert "turnstile-response" in body
    assert "203.0.113.7" in body


async def test_turnstile_refuses_an_invalid_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-response"]})

    async with mock_client(handler) as client:
        assert await verify_turnstile("test-secret", "bad", "203.0.113.7", client=client) is False


async def test_turnstile_fails_closed_on_a_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    async with mock_client(handler) as client:
        assert await verify_turnstile("test-secret", "turnstile-response", "203.0.113.7", client=client) is False


async def test_turnstile_fails_closed_on_a_server_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"success": True})

    async with mock_client(handler) as client:
        assert await verify_turnstile("test-secret", "turnstile-response", "203.0.113.7", client=client) is False


async def test_turnstile_fails_closed_on_a_malformed_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    async with mock_client(handler) as client:
        assert await verify_turnstile("test-secret", "turnstile-response", "203.0.113.7", client=client) is False


async def test_turnstile_refuses_an_empty_response_without_calling_cloudflare() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request must be sent without a Turnstile response")

    async with mock_client(handler) as client:
        assert await verify_turnstile("test-secret", "", "203.0.113.7", client=client) is False
        assert await verify_turnstile("test-secret", None, "203.0.113.7", client=client) is False


async def test_turnstile_is_disabled_without_a_secret() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request must be sent when Turnstile is not configured")

    async with mock_client(handler) as client:
        assert await verify_turnstile(None, None, None, client=client) is True
        assert await verify_turnstile("", "", None, client=client) is True


def test_turnstile_module_is_the_siteverify_client() -> None:
    assert captcha.TURNSTILE_URL == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
