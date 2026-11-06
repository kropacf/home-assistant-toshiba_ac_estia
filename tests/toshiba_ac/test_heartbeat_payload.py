import asyncio
import unittest
from unittest.mock import MagicMock

from custom_components.toshiba_ac_community.toshiba_ac.device import ToshibaAcDevice

# A state reported by a real unit; the indoor/outdoor temperatures it carries
# are both 23, so a heartbeat that reports other values is visible as a change.
AC_STATE = "30421941816400101717fe0200001002000000"


def _device() -> ToshibaAcDevice:
    return ToshibaAcDevice(
        "AC_TEST",
        "device_id",
        "ac_id",
        "ac_unique_id",
        AC_STATE,
        "1.0.0",
        "0000",
        "1",
        MagicMock(),
        MagicMock(),
    )


class HeartbeatPayloadTest(unittest.TestCase):
    """A malformed heartbeat value must not take the whole payload down."""

    def setUp(self) -> None:
        self.device = _device()
        self.state_changed_calls = 0

        def _on_state_changed(_: ToshibaAcDevice) -> None:
            self.state_changed_calls += 1

        self.device.on_state_changed_callback.add(_on_state_changed)

    def test_malformed_value_does_not_raise(self) -> None:
        # "Power" arrives empty from the cloud.
        asyncio.run(self.device.handle_cmd_heartbeat({"iTemp": "18", "oTemp": "15", "Power": ""}))

    def test_valid_values_survive_a_malformed_one(self) -> None:
        asyncio.run(self.device.handle_cmd_heartbeat({"iTemp": "18", "oTemp": "15", "Power": ""}))
        self.assertEqual(self.device.fcu_state.ac_indoor_temperature, 24)
        self.assertEqual(self.device.fcu_state.ac_outdoor_temperature, 21)

    def test_non_hex_value_is_dropped(self) -> None:
        asyncio.run(self.device.handle_cmd_heartbeat({"iTemp": "18", "oTemp": "15", "Power": "zz"}))
        self.assertEqual(self.device.fcu_state.ac_indoor_temperature, 24)

    def test_multi_byte_value_is_dropped(self) -> None:
        asyncio.run(self.device.handle_cmd_heartbeat({"iTemp": "18", "oTemp": "15", "Power": "0102"}))
        self.assertEqual(self.device.fcu_state.ac_indoor_temperature, 24)

    def test_healthy_payload_is_unchanged(self) -> None:
        asyncio.run(self.device.handle_cmd_heartbeat({"iTemp": "18", "oTemp": "15", "Power": "01"}))
        self.assertEqual(self.device.fcu_state.ac_indoor_temperature, 24)
        self.assertEqual(self.device.fcu_state.ac_outdoor_temperature, 21)
        self.assertEqual(self.state_changed_calls, 1)

    def test_fully_malformed_payload_is_ignored(self) -> None:
        asyncio.run(self.device.handle_cmd_heartbeat({"Power": ""}))
        self.assertEqual(self.state_changed_calls, 0)

    def test_temperatures_keep_their_sign(self) -> None:
        asyncio.run(self.device.handle_cmd_heartbeat({"iTemp": "f6"}))
        self.assertEqual(self.device.fcu_state.ac_indoor_temperature, -10)


if __name__ == "__main__":
    unittest.main()
