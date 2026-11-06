import unittest
from unittest.mock import AsyncMock, patch

from custom_components.toshiba_ac_community.toshiba_ac.device_manager import (
    ToshibaAcDeviceManager,
)
from custom_components.toshiba_ac_community.toshiba_ac.utils.http_api import (
    ToshibaAcHttpApi,
)


class StableDeviceIdTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_sessions_reuse_device_id(self) -> None:
        api = ToshibaAcHttpApi("user", "password", "0123456789abcdef")

        await api._ensure_session()
        session = api.session
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.headers["Device-ID"], "0123456789abcdef")
        self.assertEqual(session.headers["User-Agent"], "")
        await api.shutdown()

        await api._ensure_session()
        session = api.session
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.headers["Device-ID"], "0123456789abcdef")
        self.assertEqual(session.headers["User-Agent"], "")
        await api.shutdown()

    async def test_manager_passes_raw_device_id_to_http_api(self) -> None:
        with patch("custom_components.toshiba_ac_community.toshiba_ac.device_manager.ToshibaAcHttpApi") as http_api_class:
            # No cached session, so connect() must perform a login.
            http_api_class.return_value.access_token = None
            http_api_class.return_value.consumer_id = None
            http_api_class.return_value.connect = AsyncMock(side_effect=RuntimeError)
            http_api_class.return_value.shutdown = AsyncMock()
            manager = ToshibaAcDeviceManager("user", "password", "0123456789abcdef")

            with self.assertRaises(RuntimeError):
                await manager.connect()

        http_api_class.assert_called_once_with(
            "user",
            "password",
            "0123456789abcdef",
            access_token=None,
            access_token_type=None,
            consumer_id=None,
        )


if __name__ == "__main__":
    unittest.main()
