import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# custom_components.toshiba_ac_community.toshiba_ac.utils.http_api and custom_components.toshiba_ac_community.toshiba_ac.device import each other, so importing
# http_api first raises ImportError. Import the device package ahead of it, the same
# order the library itself is used in.
import custom_components.toshiba_ac_community.toshiba_ac.device_manager  # noqa: F401
from custom_components.toshiba_ac_community.toshiba_ac.utils.http_api import (
    ToshibaAcHttpApi,
    ToshibaAcHttpApiError,
    ToshibaAcHttpApiRateLimitError,
)

# 1 initial attempt + 5 retries from the rate-limit decorator.
RATE_LIMIT_ATTEMPTS = 6
# 1 initial attempt + 2 retries from the generic-error decorator.
GENERIC_ATTEMPTS = 3


def _session_returning(status: int) -> MagicMock:
    """Build a fake aiohttp session whose requests always answer with `status`."""
    response = MagicMock()
    response.status = status
    response.headers = {}
    response.text = AsyncMock(return_value="Try again in 60 seconds.")

    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.closed = False
    session.post = MagicMock(return_value=context)
    session.get = MagicMock(return_value=context)
    return session


class RateLimitStatusTest(unittest.IsolatedAsyncioTestCase):
    async def _call_login(self, status: int) -> MagicMock:
        api = ToshibaAcHttpApi("user", "password", "0123456789abcdef")
        session = _session_returning(status)
        api.session = session

        # The retry decorators sleep between attempts; we only care about how many
        # attempts each policy makes, not about waiting through the backoff.
        with (
            patch("custom_components.toshiba_ac_community.toshiba_ac.utils.asyncio.sleep", AsyncMock()),
            patch("custom_components.toshiba_ac_community.toshiba_ac.utils.http_api.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(ToshibaAcHttpApiError) as caught:
                await api.request_api(
                    api.LOGIN_PATH,
                    post={"Username": "user", "Password": "password"},
                    headers={"Content-Type": "application/json"},
                )

        self.exception = caught.exception
        return session

    async def test_429_is_treated_as_rate_limit(self) -> None:
        session = await self._call_login(429)

        self.assertIsInstance(self.exception, ToshibaAcHttpApiRateLimitError)
        self.assertEqual(session.post.call_count, RATE_LIMIT_ATTEMPTS)

    async def test_403_is_treated_as_rate_limit(self) -> None:
        session = await self._call_login(403)

        self.assertIsInstance(self.exception, ToshibaAcHttpApiRateLimitError)
        self.assertEqual(session.post.call_count, RATE_LIMIT_ATTEMPTS)

    async def test_other_errors_keep_the_generic_retry_policy(self) -> None:
        session = await self._call_login(500)

        self.assertNotIsInstance(self.exception, ToshibaAcHttpApiRateLimitError)
        self.assertEqual(session.post.call_count, GENERIC_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
