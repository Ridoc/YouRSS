"""Password verification and key isolation (C5.I1)."""

from pydantic import SecretStr

from yourss.accounts import create_session_cookie, verify_session_cookie
from yourss.schema import Page, Password, PasswordMethod, User
from yourss.security import check_login, login_token, verify_password, verify_password_async


def user(password: str = "correct horse") -> User:  # noqa: S107 - test fixture
    return User(
        name="bob",
        password=Password(method=PasswordMethod.CLEAR, value=SecretStr(password)),
        pages=[Page(name="Home", channels=["UC" + "a" * 22])],
    )


def test_clear_password_comparison_handles_unicode() -> None:
    assert verify_password(user("pässwörd"), "pässwörd")
    assert not verify_password(user("pässwörd"), "passwörd")


async def test_async_verification_uses_the_same_rule() -> None:
    assert await verify_password_async(user(), "correct horse")
    assert not await verify_password_async(user(), "wrong")


def test_key_rotation_invalidates_legacy_and_session_tokens() -> None:
    account = user()
    token = login_token(account, "a" * 64)
    assert check_login(account, token, "a" * 64)
    assert not check_login(account, token, "b" * 64)
    cookie = create_session_cookie("a" * 64, 7, 1, 600)
    assert verify_session_cookie("a" * 64, cookie) == (7, 1)
    assert verify_session_cookie("b" * 64, cookie) is None
