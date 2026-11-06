import unittest

from custom_components.toshiba_ac_community.toshiba_ac.device.fcu_state import (
    ToshibaAcFcuState,
)
from custom_components.toshiba_ac_community.toshiba_ac.device.properties import (
    ToshibaAcFanMode,
    ToshibaAcSwingMode,
)

# Reported by a 2026 Shorai Curve: swing byte 0x81, which no legacy table knows.
SHORAI_CURVE_STATE = "30421941816400101717fe0200001002000000"


class UnknownRawValueTest(unittest.TestCase):
    """An unrecognised raw byte must decode to NONE, never raise."""

    def test_unknown_value_does_not_raise(self) -> None:
        self.assertEqual(ToshibaAcFcuState.AcFanMode.from_raw(0x99), ToshibaAcFanMode.NONE)

    def test_known_values_are_unchanged(self) -> None:
        self.assertEqual(ToshibaAcFcuState.AcFanMode.from_raw(0x41), ToshibaAcFanMode.AUTO)
        self.assertEqual(ToshibaAcFcuState.AcSwingMode.from_raw(0x60), ToshibaAcSwingMode.HADA)

    def test_state_with_unknown_byte_is_printable(self) -> None:
        # __str__ reads every decoded property, which is how one bad byte used to take
        # down state handling and entity setup alike.
        state = ToshibaAcFcuState.from_hex_state(SHORAI_CURVE_STATE.replace("8164", "c064"))
        self.assertIn("AcSwingMode: NONE", str(state))


class IndependentAxisSwingTest(unittest.TestCase):
    """0x80-0xB6 encodes vertical and horizontal vanes independently."""

    def test_reported_crashing_value_decodes(self) -> None:
        state = ToshibaAcFcuState.from_hex_state(SHORAI_CURVE_STATE)
        self.assertEqual(state.ac_swing_mode, ToshibaAcSwingMode.FIXED_1)

    def test_vertical_fixed_positions(self) -> None:
        for raw, expected in (
            (0x81, ToshibaAcSwingMode.FIXED_1),
            (0x82, ToshibaAcSwingMode.FIXED_2),
            (0x83, ToshibaAcSwingMode.FIXED_3),
            (0x84, ToshibaAcSwingMode.FIXED_4),
            (0x85, ToshibaAcSwingMode.FIXED_5),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(ToshibaAcFcuState.AcSwingMode.from_raw(raw), expected)

    def test_horizontal_position_does_not_hide_vertical_one(self) -> None:
        # 0xA9: horizontal fixed 5, vertical fixed 1. The legacy enum has no horizontal
        # fixed position, so the vertical one is what gets reported.
        self.assertEqual(ToshibaAcFcuState.AcSwingMode.from_raw(0xA9), ToshibaAcSwingMode.FIXED_1)

    def test_swinging_axes(self) -> None:
        for raw, expected in (
            (0x86, ToshibaAcSwingMode.SWING_VERTICAL),
            (0xB1, ToshibaAcSwingMode.SWING_HORIZONTAL),
            (0xB0, ToshibaAcSwingMode.SWING_HORIZONTAL),
            (0xB6, ToshibaAcSwingMode.SWING_VERTICAL_AND_HORIZONTAL),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(ToshibaAcFcuState.AcSwingMode.from_raw(raw), expected)

    def test_both_axes_idle_is_off(self) -> None:
        self.assertEqual(ToshibaAcFcuState.AcSwingMode.from_raw(0x80), ToshibaAcSwingMode.OFF)

    def test_out_of_range_bits_are_unknown(self) -> None:
        # 0x87 sits in the range but has no axis 7, so it stays unknown rather than
        # being decoded as something plausible-looking.
        self.assertEqual(ToshibaAcFcuState.AcSwingMode.from_raw(0x87), ToshibaAcSwingMode.NONE)
        self.assertEqual(ToshibaAcFcuState.AcSwingMode.from_raw(0xC0), ToshibaAcSwingMode.NONE)

    def test_legacy_encoding_still_wins(self) -> None:
        self.assertEqual(ToshibaAcFcuState.AcSwingMode.from_raw(0x41), ToshibaAcSwingMode.SWING_VERTICAL)
        self.assertEqual(ToshibaAcFcuState.AcSwingMode.from_raw(0x31), ToshibaAcSwingMode.OFF)

    def test_sending_still_uses_legacy_presets(self) -> None:
        # New units accept the legacy commands, so nothing changes on the way out.
        self.assertEqual(ToshibaAcFcuState.AcSwingMode.to_raw(ToshibaAcSwingMode.SWING_VERTICAL), 0x41)


if __name__ == "__main__":
    unittest.main()
