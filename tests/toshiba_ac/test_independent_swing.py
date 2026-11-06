import unittest

from custom_components.toshiba_ac_community.toshiba_ac.device.fcu_state import ToshibaAcFcuState
from custom_components.toshiba_ac_community.toshiba_ac.device.properties import (
    ToshibaAcHorizontalSwingMode,
    ToshibaAcSwingMode,
    ToshibaAcVerticalSwingMode,
)

CODEC = ToshibaAcFcuState.AcIndependentSwingMode

VERTICAL = [
    ToshibaAcVerticalSwingMode.NONE,
    ToshibaAcVerticalSwingMode.FIXED_1,
    ToshibaAcVerticalSwingMode.FIXED_2,
    ToshibaAcVerticalSwingMode.FIXED_3,
    ToshibaAcVerticalSwingMode.FIXED_4,
    ToshibaAcVerticalSwingMode.FIXED_5,
    ToshibaAcVerticalSwingMode.SWING,
]
HORIZONTAL = [
    ToshibaAcHorizontalSwingMode.NONE,
    ToshibaAcHorizontalSwingMode.FIXED_1,
    ToshibaAcHorizontalSwingMode.FIXED_2,
    ToshibaAcHorizontalSwingMode.FIXED_3,
    ToshibaAcHorizontalSwingMode.FIXED_4,
    ToshibaAcHorizontalSwingMode.FIXED_5,
    ToshibaAcHorizontalSwingMode.SWING,
]


class IndependentSwingCodecTest(unittest.TestCase):
    def test_all_49_combinations_round_trip(self) -> None:
        for h_bits, horizontal in enumerate(HORIZONTAL):
            for v_bits, vertical in enumerate(VERTICAL):
                raw = 0x80 | h_bits << 3 | v_bits
                with self.subTest(raw=hex(raw)):
                    self.assertTrue(CODEC.is_independent_raw(raw))
                    self.assertIs(CODEC.vertical_from_raw(raw), vertical)
                    self.assertIs(CODEC.horizontal_from_raw(raw), horizontal)
                    self.assertEqual(CODEC.to_raw(vertical, horizontal), raw)

    def test_reported_values_from_issue_2(self) -> None:
        # 0x81 = vertical fixed 1 (integration issue #32), 0xA3 = horizontal
        # fixed 4 + vertical fixed 3 (10-series report), 0xB6 = both swinging.
        self.assertIs(CODEC.vertical_from_raw(0x81), ToshibaAcVerticalSwingMode.FIXED_1)
        self.assertIs(CODEC.horizontal_from_raw(0x81), ToshibaAcHorizontalSwingMode.NONE)
        self.assertIs(CODEC.vertical_from_raw(0xA3), ToshibaAcVerticalSwingMode.FIXED_3)
        self.assertIs(CODEC.horizontal_from_raw(0xA3), ToshibaAcHorizontalSwingMode.FIXED_4)
        self.assertIs(CODEC.vertical_from_raw(0xB6), ToshibaAcVerticalSwingMode.SWING)
        self.assertIs(CODEC.horizontal_from_raw(0xB6), ToshibaAcHorizontalSwingMode.SWING)

    def test_axis_values_out_of_range_are_not_independent(self) -> None:
        for raw in (0x87, 0x8F, 0xB7, 0x7F, 0x31, 0x41, 0x60, 0x00, 0xFF):
            with self.subTest(raw=hex(raw)):
                self.assertFalse(CODEC.is_independent_raw(raw))

    def test_legacy_bytes_read_as_axis_positions(self) -> None:
        self.assertIs(CODEC.vertical_from_raw(0x41), ToshibaAcVerticalSwingMode.SWING)
        self.assertIs(CODEC.horizontal_from_raw(0x41), ToshibaAcHorizontalSwingMode.NONE)
        self.assertIs(CODEC.vertical_from_raw(0x42), ToshibaAcVerticalSwingMode.NONE)
        self.assertIs(CODEC.horizontal_from_raw(0x42), ToshibaAcHorizontalSwingMode.SWING)
        self.assertIs(CODEC.vertical_from_raw(0x43), ToshibaAcVerticalSwingMode.SWING)
        self.assertIs(CODEC.horizontal_from_raw(0x43), ToshibaAcHorizontalSwingMode.SWING)
        self.assertIs(CODEC.vertical_from_raw(0x52), ToshibaAcVerticalSwingMode.FIXED_3)
        self.assertIs(CODEC.vertical_from_raw(0x31), ToshibaAcVerticalSwingMode.NONE)
        self.assertIs(CODEC.horizontal_from_raw(0x60), ToshibaAcHorizontalSwingMode.NONE)


class FcuStateIndependentSwingTest(unittest.TestCase):
    def test_set_and_read_back(self) -> None:
        state = ToshibaAcFcuState()
        state.set_ac_independent_swing(ToshibaAcVerticalSwingMode.FIXED_2, ToshibaAcHorizontalSwingMode.SWING)
        self.assertTrue(state.uses_independent_swing)
        self.assertIs(state.ac_vertical_swing_mode, ToshibaAcVerticalSwingMode.FIXED_2)
        self.assertIs(state.ac_horizontal_swing_mode, ToshibaAcHorizontalSwingMode.SWING)
        # The combined legacy view keeps working on the same byte.
        self.assertIs(state.ac_swing_mode, ToshibaAcSwingMode.SWING_HORIZONTAL)

    def test_legacy_setter_leaves_independent_flag_off(self) -> None:
        state = ToshibaAcFcuState()
        state.ac_swing_mode = ToshibaAcSwingMode.SWING_VERTICAL
        self.assertFalse(state.uses_independent_swing)
        self.assertIs(state.ac_vertical_swing_mode, ToshibaAcVerticalSwingMode.SWING)


if __name__ == "__main__":
    unittest.main()
