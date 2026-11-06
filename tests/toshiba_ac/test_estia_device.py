"""Tests for the Estia device and for how the manager routes Estia messages."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.toshiba_ac_community.toshiba_ac.device.estia import (
    ToshibaEstiaDevice,
)
from custom_components.toshiba_ac_community.toshiba_ac.device_manager import (
    ToshibaAcDeviceManager,
)
from custom_components.toshiba_ac_community.toshiba_ac.utils.http_api import (
    ToshibaAcDeviceInfo,
)

# Idle unit, heating mode, outdoor 16.5 C - see test_estia_fcu_state for the full decode
STATE = "0c8800000206484800000002064848000000200062626666515cff000000884848484e52"

# A real CMD_HEARTBEAT_ESTIA payload
HEARTBEAT = {
    "roomWater_temp": "61",
    "TWI_temp": "61",
    "TWO_temp": "66",
    "THO_temp": "66",
    "TO_temp": "4f",
    "TFI_temp": "5c",
    "Comp": "00",
    "Heater": "00",
    "FLO": "0000",
    "Power": "0000000b",
}


def make_device() -> ToshibaEstiaDevice:
    return ToshibaEstiaDevice(
        "AW-1",
        "user_0123456789abcdef",
        "ac-id",
        "unique-id",
        STATE,
        "1.2.3",
        "0000000",
        "1",
        MagicMock(),
        MagicMock(),
    )


class EstiaDeviceTest(unittest.IsolatedAsyncioTestCase):
    async def test_sensors_have_values_before_the_first_poll(self) -> None:
        device = make_device()

        self.assertEqual(device.to_temperature, 16.5)
        self.assertEqual(device.tfi_temperature, 22.0)
        self.assertEqual(device.dhw_target_temperature, 52.0)

    async def test_heartbeat_updates_water_sensors_and_flow_rate(self) -> None:
        device = make_device()

        await device.handle_cmd_heartbeat_estia(HEARTBEAT)

        self.assertEqual(device.to_temperature, 15.5)
        self.assertEqual(device.twi_temperature, 24.5)
        self.assertEqual(device.two_temperature, 27.0)
        self.assertEqual(device.water_flow_rate, 0.0)

    async def test_heartbeat_notifies_only_when_something_moved(self) -> None:
        device = make_device()
        callback = AsyncMock()
        device.on_state_changed_callback.add(callback)

        await device.handle_cmd_heartbeat_estia(HEARTBEAT)
        self.assertEqual(callback.await_count, 1)

        await device.handle_cmd_heartbeat_estia(HEARTBEAT)
        self.assertEqual(callback.await_count, 1)

    async def test_malformed_heartbeat_field_does_not_raise(self) -> None:
        device = make_device()

        await device.handle_cmd_heartbeat_estia({**HEARTBEAT, "TO_temp": "not hex"})

        self.assertIsNone(device.to_temperature)

    async def test_flow_rate_is_unknown_until_a_heartbeat_arrives(self) -> None:
        # The status string does not carry it, so it must not read as 0 l/min
        device = make_device()

        self.assertIsNone(device.water_flow_rate)

    async def test_state_push_refreshes_the_water_sensors_too(self) -> None:
        device = make_device()

        # Only the outdoor temperature byte changes, to 17 C
        await device.handle_cmd_hdu_from_estia({"data": "ff" * 24 + "52" + "ff" * 11})

        self.assertEqual(device.to_temperature, 17.0)

    async def test_offline_is_reported_as_a_state_change(self) -> None:
        device = make_device()
        callback = AsyncMock()
        device.on_state_changed_callback.add(callback)

        self.assertFalse(device.is_online)

        await device.handle_connection_state(True)

        self.assertTrue(device.is_online)
        self.assertEqual(callback.await_count, 1)

        await device.handle_connection_state(True)

        self.assertEqual(callback.await_count, 1)


class EstiaDeviceManagerTest(unittest.IsolatedAsyncioTestCase):
    async def make_manager(self, estia_devices: list) -> ToshibaAcDeviceManager:
        manager = ToshibaAcDeviceManager("user", "password", "0123456789abcdef")
        manager.http_api = MagicMock()
        manager.http_api.get_devices = AsyncMock(return_value=[])
        manager.http_api.get_estia_devices = AsyncMock(return_value=estia_devices)
        manager.http_api.get_device_connection_state = AsyncMock(return_value={})
        manager.amqp_api = MagicMock()

        with patch.object(ToshibaEstiaDevice, "connect", AsyncMock()):
            await manager._discover_devices()

        return manager

    async def test_estia_devices_are_kept_apart_from_ac_devices(self) -> None:
        info = ToshibaAcDeviceInfo("ac-id", "unique-id", "AW-1", STATE, "1.2.3", "0000000", "1")
        manager = await self.make_manager([info])

        self.assertEqual(await manager.get_devices(), [])
        self.assertEqual(len(await manager.get_estia_devices()), 1)

    async def test_estia_messages_reach_the_right_device(self) -> None:
        info = ToshibaAcDeviceInfo("ac-id", "unique-id", "AW-1", STATE, "1.2.3", "0000000", "1")
        manager = await self.make_manager([info])
        device = manager.estia_devices["unique-id"]

        with patch.object(device, "handle_cmd_heartbeat_estia", AsyncMock()) as handler:
            manager.handle_cmd_heartbeat_estia("unique-id", "msg", [], HEARTBEAT, "ts")
            handler.assert_called_once_with(HEARTBEAT)

        with patch.object(device, "handle_cmd_hdu_from_estia", AsyncMock()) as handler:
            manager.handle_cmd_hdu_from_estia("unique-id", "msg", [], {"data": STATE}, "ts")
            handler.assert_called_once_with({"data": STATE})

    async def test_a_failing_estia_lookup_does_not_break_ac_setup(self) -> None:
        manager = ToshibaAcDeviceManager("user", "password", "0123456789abcdef")
        manager.http_api = MagicMock()
        manager.http_api.get_devices = AsyncMock(return_value=[])
        manager.http_api.get_estia_devices = AsyncMock(side_effect=Exception("no Estia here"))
        manager.amqp_api = MagicMock()

        await manager._discover_devices()

        self.assertEqual(await manager.get_estia_devices(), [])


if __name__ == "__main__":
    unittest.main()
