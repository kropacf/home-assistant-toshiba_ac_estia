import unittest

from custom_components.toshiba_ac_community.toshiba_ac.device.fcu_state import (
    ToshibaAcFcuState,
)


class AcTemperatureToRawTest(unittest.TestCase):
    """Fractional setpoints must round rather than raise KeyError."""

    def test_integers_are_unchanged(self) -> None:
        for temperature in (-2, 0, 5, 17, 22, 25, 30, 100):
            with self.subTest(temperature=temperature):
                self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(temperature), temperature)

    def test_sentinels_are_preserved(self) -> None:
        self.assertEqual(
            ToshibaAcFcuState.AcTemperature.to_raw(None),
            ToshibaAcFcuState.NONE_VAL_SIGNED,
        )
        # -1 is reserved as the "unset" marker on the wire, so it is remapped to 126.
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(-1), 126)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(-1.0), 126)

    def test_whole_floats_round_trip(self) -> None:
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(22.0), 22)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(-3.0), -3)

    def test_float_arithmetic_noise_is_absorbed(self) -> None:
        # Values like these come from thermostats computing setpoints in floats.
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(21.999999999999986), 22)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(19.499999999999996), 19)

    def test_fractions_round_to_nearest(self) -> None:
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(17.7), 18)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(22.4), 22)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(22.6), 23)

    def test_halves_round_half_up_not_to_even(self) -> None:
        # round() uses banker's rounding, sending an exact .5 to the nearest even
        # integer: it would give 22 for both 21.5 and 22.5, and 24 for both 23.5
        # and 24.5. Half-up sends every .5 the same way instead.
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(21.5), 22)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(22.5), 23)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(23.5), 24)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(24.5), 25)

    def test_negative_fractions_round_away_from_zero_on_halves(self) -> None:
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(-2.5), -3)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(-2.4), -2)
        self.assertEqual(ToshibaAcFcuState.AcTemperature.to_raw(-2.6), -3)

    def test_out_of_range_still_raises(self) -> None:
        # Rounding must not silently accept values the wire format cannot hold.
        with self.assertRaises(KeyError):
            ToshibaAcFcuState.AcTemperature.to_raw(200)
        with self.assertRaises(KeyError):
            ToshibaAcFcuState.AcTemperature.to_raw(200.4)

    def test_setter_accepts_fractional_value(self) -> None:
        state = ToshibaAcFcuState()
        state.ac_temperature = 23.5
        self.assertEqual(state.ac_temperature, 24)


if __name__ == "__main__":
    unittest.main()
