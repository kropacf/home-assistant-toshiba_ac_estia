import unittest
from unittest.mock import AsyncMock, patch

from custom_components.toshiba_ac_community.toshiba_ac.device_manager import (
    ToshibaAcDeviceManager,
)
from custom_components.toshiba_ac_community.toshiba_ac.utils.http_api import (
    ToshibaAcHttpApi,
)

CACHED = ("cached-token", "Bearer", "consumer-1")


class AccessTokenCacheTest(unittest.IsolatedAsyncioTestCase):
    def _manager(self, **kwargs: str) -> ToshibaAcDeviceManager:
        return ToshibaAcDeviceManager("user", "password", "0123456789abcdef", **kwargs)

    async def test_seeded_session_skips_login(self) -> None:
        access_token, token_type, consumer_id = CACHED
        manager = self._manager(access_token=access_token, access_token_type=token_type, consumer_id=consumer_id)

        with (
            patch.object(ToshibaAcHttpApi, "connect", AsyncMock()) as login,
            patch.object(ToshibaAcHttpApi, "register_client", AsyncMock(return_value="sas")),
            patch("custom_components.toshiba_ac_community.toshiba_ac.device_manager.ToshibaAcAmqpApi") as amqp,
        ):
            amqp.return_value.connect = AsyncMock()
            await manager.connect()

        login.assert_not_awaited()
        assert manager.http_api is not None
        self.assertEqual(manager.http_api.access_token, access_token)
        self.assertEqual(manager.http_api.consumer_id, consumer_id)

    async def test_without_cache_it_still_logs_in(self) -> None:
        manager = self._manager()

        with (
            patch.object(ToshibaAcHttpApi, "connect", AsyncMock()) as login,
            patch.object(ToshibaAcHttpApi, "register_client", AsyncMock(return_value="sas")),
            patch("custom_components.toshiba_ac_community.toshiba_ac.device_manager.ToshibaAcAmqpApi") as amqp,
        ):
            amqp.return_value.connect = AsyncMock()
            await manager.connect()

        login.assert_awaited_once()

    async def test_sas_token_is_still_fetched_when_not_supplied(self) -> None:
        access_token, token_type, consumer_id = CACHED
        manager = self._manager(access_token=access_token, access_token_type=token_type, consumer_id=consumer_id)

        with (
            patch.object(ToshibaAcHttpApi, "connect", AsyncMock()),
            patch.object(ToshibaAcHttpApi, "register_client", AsyncMock(return_value="fresh-sas")) as register,
            patch("custom_components.toshiba_ac_community.toshiba_ac.device_manager.ToshibaAcAmqpApi") as amqp,
        ):
            amqp.return_value.connect = AsyncMock()
            await manager.connect()

        register.assert_awaited_once()
        self.assertEqual(manager.sas_token, "fresh-sas")

    async def test_login_notifies_subscribers_so_the_session_can_be_persisted(self) -> None:
        api = ToshibaAcHttpApi("user", "password", "0123456789abcdef")
        seen = []

        async def remember(token: str, token_type: str, consumer_id: str) -> None:
            seen.append((token, token_type, consumer_id))

        api.on_access_token_updated = remember
        with patch.object(
            ToshibaAcHttpApi,
            "request_api",
            AsyncMock(
                return_value={
                    "access_token": "new-token",
                    "token_type": "Bearer",
                    "consumerId": "consumer-2",
                }
            ),
        ):
            await api.connect()

        self.assertEqual(seen, [("new-token", "Bearer", "consumer-2")])


if __name__ == "__main__":
    unittest.main()
