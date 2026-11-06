"""Tests for decoding the state of an Estia heat pump.

The status strings below are real, captured from an AW-x unit half an hour apart.
Their decoded values were cross-checked against the values the same unit reported
over the AMQP heartbeat and the extended info endpoint at the same time.
"""

import unittest

from custom_components.toshiba_ac_community.toshiba_ac.device.estia_fcu_state import (
    ToshibaEstiaFcuState,
)
from custom_components.toshiba_ac_community.toshiba_ac.device.properties import (
    EstiaCompressorStatus,
    EstiaWaterMode,
)

# Idle, heating mode, hot water target 52 C, zone 1 target 20 C, outdoor 16.5 C
STATE_AT_10_00 = "0c8800000206484800000002064848000000200062626666515cff000000884848484e52"
# The same unit 30 minutes later - only the outdoor temperature byte moved, to 17 C
STATE_AT_10_30 = "0c8800000206484800000002064848000000200062626666525cff000000884848484e52"


# Heating curve on at -1 C outside (captured 2026-11-06 23:16): manual targets
# 33 C / 27 C stay in bytes 7 / 14, the curve values 35.5 C / 26.5 C appear in
# the applied-target bytes 8 / 15
STATE_HEATING_CURVE = "0c7a000003066267010001030656550100012006696b6b6b2e60ff0000007a6256484e52"
class EstiaFcuStateTest(unittest.TestCase):
    def test_decodes_setpoints_and_mode(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AT_10_00)

        self.assertEqual(state.dhw_target_temperature, 52.0)
        self.assertEqual(state.zone1_target_temperature, 20.0)
        self.assertEqual(state.zone1_mode, EstiaWaterMode.HEAT)

    def test_decodes_an_idle_unit(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AT_10_00)

        self.assertEqual(state.compressor_status, EstiaCompressorStatus.OFF)
        self.assertFalse(state.water_pump_is_running)
        self.assertFalse(state.electric_coil_dhw_is_active)
        self.assertFalse(state.electric_coil_heat_is_active)

    def test_decodes_water_temperatures(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AT_10_00)
        temperatures = state.water_temperatures

        self.assertEqual(ToshibaEstiaFcuState.WaterTemperature.from_raw(temperatures.room_water), 25.0)
        self.assertEqual(ToshibaEstiaFcuState.WaterTemperature.from_raw(temperatures.twi), 25.0)
        self.assertEqual(ToshibaEstiaFcuState.WaterTemperature.from_raw(temperatures.two), 27.0)
        self.assertEqual(ToshibaEstiaFcuState.WaterTemperature.from_raw(temperatures.tho), 27.0)
        self.assertEqual(ToshibaEstiaFcuState.WaterTemperature.from_raw(temperatures.to), 16.5)
        self.assertEqual(ToshibaEstiaFcuState.WaterTemperature.from_raw(temperatures.tfi), 22.0)

    def test_update_reports_a_real_change(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AT_10_00)

        self.assertTrue(state.update(STATE_AT_10_30))
        self.assertEqual(ToshibaEstiaFcuState.WaterTemperature.from_raw(state.water_temperatures.to), 17.0)

    def test_update_reports_no_change_for_an_identical_state(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AT_10_00)

        self.assertFalse(state.update(STATE_AT_10_00))

    def test_partial_update_keeps_the_bytes_it_does_not_carry(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AT_10_00)

        # What a push looks like: every byte the unit did not change is 0xFF.
        # Here only the compressor turns on for hot water (byte 3).
        diff = "ffff01" + "ff" * 33

        self.assertTrue(state.update(diff))
        self.assertEqual(state.compressor_status, EstiaCompressorStatus.DHW)
        # Untouched bytes survived the merge
        self.assertEqual(state.dhw_target_temperature, 52.0)
        self.assertEqual(state.zone1_mode, EstiaWaterMode.HEAT)
        self.assertEqual(ToshibaEstiaFcuState.WaterTemperature.from_raw(state.water_temperatures.to), 16.5)

    def test_short_update_does_not_truncate_the_state(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AT_10_00)

        self.assertTrue(state.update("ffff01"))
        self.assertEqual(state.compressor_status, EstiaCompressorStatus.DHW)
        self.assertEqual(len(state.status_string), len(STATE_AT_10_00))
        self.assertEqual(ToshibaEstiaFcuState.WaterTemperature.from_raw(state.water_temperatures.tfi), 22.0)

    def test_malformed_state_is_ignored_rather_than_raising(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AT_10_00)

        self.assertFalse(state.update("not hex at all"))
        # The last good state is still there
        self.assertEqual(state.dhw_target_temperature, 52.0)

    def test_too_short_state_is_ignored(self) -> None:
        state = ToshibaEstiaFcuState()

        self.assertFalse(state.update("0c8800"))
        self.assertIsNone(state.dhw_target_temperature)

    def test_unknown_water_mode_degrades_to_none(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AT_10_00)

        # Byte 6 is the operation mode; 0x09 has never been seen on the wire
        self.assertTrue(state.update("ffffffffff09" + "ff" * 30))
        self.assertEqual(state.zone1_mode, EstiaWaterMode.NONE)

    def test_unwired_sensor_reads_as_no_value(self) -> None:
        # A sensor the installation does not have reports 0, which would otherwise
        # decode to a plausible looking -24 C
        self.assertIsNone(ToshibaEstiaFcuState.WaterTemperature.from_raw(0))
        self.assertIsNone(ToshibaEstiaFcuState.WaterTemperature.from_raw(0xFF))
        self.assertIsNone(ToshibaEstiaFcuState.WaterTemperature.from_raw(None))

    def test_decodes_applied_targets_separately_from_manual_targets(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_HEATING_CURVE)

        self.assertEqual(state.zone1_target_temperature, 33.0)
        self.assertEqual(state.zone1_applied_temperature, 35.5)
        self.assertEqual(state.zone2_target_temperature, 27.0)
        self.assertEqual(state.zone2_applied_temperature, 26.5)

    def test_applied_targets_follow_a_status_diff(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_HEATING_CURVE)
        # Unit-initiated diff: applied targets back to the manual 33 C / 27 C
        state.update("ffffffffffffff62ffffffffffff56" + "ff" * 21)

        self.assertEqual(state.zone1_applied_temperature, 33.0)
        self.assertEqual(state.zone2_applied_temperature, 27.0)
        self.assertEqual(state.zone1_target_temperature, 33.0)


if __name__ == "__main__":
    unittest.main()
