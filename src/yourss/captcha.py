"""Cloudflare Turnstile verification; fail closed when configured."""

from __future__ import annotations

import httpx

TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile(
    secret: str | None,
    response: str | None,
    remote_ip: str | None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    if not secret:
        return True
    if not response:
        return False
    payload = {"secret": secret, "response": response}
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=5.0) as owned:
                reply = await owned.post(TURNSTILE_URL, data=payload)
        else:
            reply = await client.post(TURNSTILE_URL, data=payload)
        if reply.status_code != 200:
            return False
        body = reply.json()
        return bool(body.get("success"))
    except Exception:
        # Timeout, DNS, malformed JSON, 5xx: never let an unverified request through.
        return False
