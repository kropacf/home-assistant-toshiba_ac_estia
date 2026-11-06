import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# custom_components.toshiba_ac_community.toshiba_ac.utils.http_api and custom_components.toshiba_ac_community.toshiba_ac.device import each other, so importing
# http_api first raises ImportError. Import the device package ahead of it, the same
# order the library itself is used in.
import custom_components.toshiba_ac_community.toshiba_ac.device_manager  # noqa: F401
from custom_components.toshiba_ac_community.toshiba_ac.utils.http_api import (
    ToshibaAcHttpApi,
    ToshibaAcHttpApiAuthError,
    ToshibaAcHttpApiError,
)

# 1 initial attempt + 2 retries from the generic-error decorator.
GENERIC_ATTEMPTS = 3

# The exact message Toshiba returned for a wrong password on 2026-08-26. Its
# StatusCode was NOT "InvalidUserNameorPassword", so the error fell through to
# the generic retryable ToshibaAcHttpApiError and the retry decorator burned
# 3 of the 4 login attempts Toshiba allows before locking the account.
WRONG_PASSWORD_MESSAGE = "Invalid password. 3 attempts remaining before account lockout."
LOCKED_MESSAGE = "Your account is locked. Please try again later."


def _session_returning_json(body: dict) -> MagicMock:
    """Build a fake aiohttp session whose requests answer 200 with `body`."""
    response = MagicMock()
    response.status = 200
    response.headers = {}
    response.json = AsyncMock(return_value=body)

    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.closed = False
    session.post = MagicMock(return_value=context)
    session.get = MagicMock(return_value=context)
    return session


class AuthErrorNoRetryTest(unittest.IsolatedAsyncioTestCase):
    async def _call(self, path: str, status_code: str, message: str) -> MagicMock:
        api = ToshibaAcHttpApi("user", "password", "0123456789abcdef")
        session = _session_returning_json({"IsSuccess": False, "StatusCode": status_code, "Message": message})
        api.session = session

        with (
            patch("custom_components.toshiba_ac_community.toshiba_ac.utils.asyncio.sleep", AsyncMock()),
            patch("custom_components.toshiba_ac_community.toshiba_ac.utils.http_api.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(ToshibaAcHttpApiError) as caught:
                await api.request_api(
                    path,
                    post={"Username": "user", "Password": "password"},
                    headers={"Content-Type": "application/json"},
                )

        self.exception = caught.exception
        return session

    async def _call_login(self, status_code: str, message: str) -> MagicMock:
        return await self._call(ToshibaAcHttpApi.LOGIN_PATH, status_code, message)

    async def test_wrong_password_fails_fast(self) -> None:
        session = await self._call_login("InvalidPassword", WRONG_PASSWORD_MESSAGE)

        self.assertIsInstance(self.exception, ToshibaAcHttpApiAuthError)
        self.assertEqual(session.post.call_count, 1)

    async def test_locked_account_fails_fast(self) -> None:
        session = await self._call_login("AccountLocked", LOCKED_MESSAGE)

        self.assertIsInstance(self.exception, ToshibaAcHttpApiAuthError)
        self.assertEqual(session.post.call_count, 1)

    async def test_known_auth_status_code_still_fails_fast(self) -> None:
        session = await self._call_login("InvalidUserNameorPassword", "Invalid username or password")

        self.assertIsInstance(self.exception, ToshibaAcHttpApiAuthError)
        self.assertEqual(session.post.call_count, 1)

    async def test_unrelated_api_error_keeps_the_generic_retry_policy(self) -> None:
        session = await self._call_login("SomethingElse", "Temporary server problem")

        self.assertNotIsInstance(self.exception, ToshibaAcHttpApiAuthError)
        self.assertEqual(session.post.call_count, GENERIC_ATTEMPTS)

    async def test_non_login_endpoints_never_match_the_message_heuristic(self) -> None:
        # "blocked" contains "lock"; a transient WAF-style error on a data endpoint
        # must stay retryable instead of surfacing as a credentials failure.
        session = await self._call(
            ToshibaAcHttpApi.AC_STATE_PATH,
            "RequestBlocked",
            "Request temporarily blocked, please retry",
        )

        self.assertNotIsInstance(self.exception, ToshibaAcHttpApiAuthError)
        self.assertEqual(session.post.call_count, GENERIC_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
