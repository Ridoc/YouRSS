"""The catch-all must not shadow the static mount (C5.I2)."""

from httpx import AsyncClient


async def test_static_css_is_served(client: AsyncClient) -> None:
    response = await client.get("/static/yourss.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert "yourss-account" in response.text


async def test_static_favicon_is_served(client: AsyncClient) -> None:
    response = await client.get("/static/icons/favicon-32.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
