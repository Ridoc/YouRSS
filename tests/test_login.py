from time import time

from bs4 import BeautifulSoup
from httpx import AsyncClient, BasicAuth
from pydantic import SecretStr
from pytest import mark

from yourss.schema import Password, PasswordMethod, User
from yourss.security import LOGIN_COOKIE, check_login, login_token, read_config
from yourss.settings import current_config

HTMX = {"HX-Request": "true"}


def bob() -> User:
    config = read_config()
    assert config is not None
    (out,) = (user for user in config.users if user.name == "bob")
    return out


def csrf_of(html: str, form_selector: str) -> str:
    form = BeautifulSoup(html, "html.parser").select_one(form_selector)
    assert form is not None
    field = form.select_one("input[name=csrf_token]")
    assert field is not None
    return str(field["value"])


async def legacy_csrf(client: AsyncClient, path: str = "/u/bob") -> str:
    resp = await client.get(path)
    return csrf_of(resp.text, "form.yourss-login")


async def public_csrf(client: AsyncClient) -> str:
    resp = await client.get("/login")
    assert resp.status_code == 200
    return csrf_of(resp.text, "form[action='/auth/request']")


@mark.parametrize("path", ["/u/bob", "/u/bob/Homepage"])
async def test_form_instead_of_a_challenge(client: AsyncClient, path: str) -> None:
    resp = await client.get(path + "?c=UCxxx")
    assert resp.status_code == 401
    assert "www-authenticate" not in resp.headers
    (form,) = BeautifulSoup(resp.text, "html.parser").select("form.yourss-login")
    # The form posts back to the page it protects, its query string included
    assert form["action"] == path + "?c=UCxxx"
    assert form.select("input[type=password][name=password]")
    # A mutation needs the token, and the login page issues its cookie
    assert form.select("input[name=csrf_token]")
    assert client.cookies.get("yourss-csrf")


async def test_login(client: AsyncClient) -> None:
    token = await legacy_csrf(client, "http://test/u/bob/Homepage?c=UCxxx")
    resp = await client.post("http://test/u/bob/Homepage?c=UCxxx", data={"password": "foo", "csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/u/bob/Homepage?c=UCxxx"
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith(f"{LOGIN_COOKIE}=")
    assert "Path=/u/bob" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert f"Max-Age={int(current_config.login_ttl.total_seconds())}" in cookie
    # Plain HTTP here: not Secure
    assert "Secure" not in cookie

    # The cookie opens the user, its folders and their htmx fragments
    for path in ("/u/bob", "/u/bob/Homepage"):
        resp = await client.get(path)
        assert resp.status_code == 200
    resp = await client.get("/u/bob/Homepage", headers=HTMX)
    assert resp.status_code == 200


async def test_login_secure_behind_https(client: AsyncClient) -> None:
    token = await legacy_csrf(client, "https://test/u/bob")
    resp = await client.post("https://test/u/bob", data={"password": "foo", "csrf_token": token})
    assert resp.status_code == 303
    assert "Secure" in resp.headers["set-cookie"]


async def test_wrong_password(client: AsyncClient) -> None:
    token = await legacy_csrf(client)
    resp = await client.post("/u/bob", data={"password": "bar", "csrf_token": token})
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers
    soup = BeautifulSoup(resp.text, "html.parser")
    assert soup.select("form.yourss-login .yourss-login-error")


async def test_logout(client: AsyncClient) -> None:
    token = await legacy_csrf(client)
    await client.post("/u/bob", data={"password": "foo", "csrf_token": token})
    resp = await client.get("/u/bob/Homepage")
    assert resp.status_code == 200
    # The settings offer to log out of a protected user, with a CSRF token
    form = BeautifulSoup(resp.text, "html.parser").select_one("form.yourss-logout[action='/u/bob']")
    assert form is not None
    field = form.select_one("input[name=csrf_token]")
    assert field is not None
    token = str(field["value"])

    resp = await client.post("/u/bob/Homepage", data={"logout": "1", "csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/u/bob"
    assert 'yourss-login=""' in resp.headers["set-cookie"]
    assert "Max-Age=0" in resp.headers["set-cookie"]
    resp = await client.get("/u/bob")
    assert resp.status_code == 401


async def test_legacy_post_without_csrf_is_forbidden(client: AsyncClient) -> None:
    resp = await client.post("/u/bob", data={"password": "foo"})
    assert resp.status_code == 403
    assert LOGIN_COOKIE not in "".join(resp.headers.get_list("set-cookie"))
    # Nothing was logged in
    assert (await client.get("/u/bob")).status_code == 401


async def test_legacy_post_with_an_invalid_csrf_is_forbidden(client: AsyncClient) -> None:
    await legacy_csrf(client)  # Issues the yourss-csrf cookie
    resp = await client.post("/u/bob", data={"password": "foo", "csrf_token": "not-a-token"})
    assert resp.status_code == 403
    assert LOGIN_COOKIE not in "".join(resp.headers.get_list("set-cookie"))


async def test_basic_still_accepted(client: AsyncClient) -> None:
    resp = await client.get("/u/bob", auth=BasicAuth("bob", "foo"))
    assert resp.status_code == 200
    assert "yourss-login=" not in "".join(resp.headers.get_list("set-cookie"))
    resp = await client.get("/u/bob", auth=BasicAuth("bob", "bar"))
    assert resp.status_code == 401


async def test_htmx_without_login_reloads(client: AsyncClient) -> None:
    resp = await client.get("/u/bob/Homepage", headers=HTMX)
    assert resp.status_code == 401
    assert resp.headers["HX-Refresh"] == "true"


async def test_refused_cookies(client: AsyncClient, secret_key: str) -> None:
    tokens = [
        "",
        "garbage",
        "123.abc",
        # Expired
        login_token(bob(), secret_key, now=time() - 2 * current_config.login_ttl.total_seconds()),
        # Tampered expiry
        "9" + login_token(bob(), secret_key),
    ]
    for token in tokens:
        client.cookies.set(LOGIN_COOKIE, token, path="/u/bob")
        resp = await client.get("/u/bob")
        assert resp.status_code == 401


async def test_cookie_bound_to_user_and_password(secret_key: str) -> None:
    user = bob()
    token = login_token(user, secret_key)
    assert check_login(user, token, secret_key)
    # Another user, or the same one with a new password: refused
    other = user.model_copy(update={"name": "carol"})
    assert not check_login(other, token, secret_key)
    renewed = user.model_copy(update={"password": Password(method=PasswordMethod.CLEAR, value=SecretStr("new"))})
    assert not check_login(renewed, token, secret_key)
    # A user without a password never logs in
    assert not check_login(user.model_copy(update={"password": None}), token, secret_key)


async def test_user_without_password(client: AsyncClient) -> None:
    resp = await client.get("/u/alice")
    assert resp.status_code == 200
    assert not BeautifulSoup(resp.text, "html.parser").select("form.yourss-logout")
    token = await public_csrf(client)
    resp = await client.post("/u/alice/Homepage", data={"password": "foo", "csrf_token": token})
    assert resp.status_code == 303
    assert "set-cookie" not in resp.headers


async def test_unknown_user(client: AsyncClient) -> None:
    token = await public_csrf(client)
    resp = await client.post("/u/unknown", data={"password": "foo", "csrf_token": token})
    assert resp.status_code == 404
