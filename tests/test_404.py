"""Unknown paths return the HTML 404, not a 403 or a JSON detail (C5.I2)."""

from httpx import AsyncClient


async def test_unknown_path_is_html_404(client: AsyncClient) -> None:
    response = await client.get("/definitely/not/a/route")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "does not exist" in response.text


async def test_deep_unknown_path_is_404(client: AsyncClient) -> None:
    response = await client.get("/one/two/three/four")
    assert response.status_code == 404
