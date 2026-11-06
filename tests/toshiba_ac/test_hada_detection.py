import typing as t
import unittest

from custom_components.toshiba_ac_community.toshiba_ac.device import ToshibaAcDevice
from custom_components.toshiba_ac_community.toshiba_ac.device.features import ToshibaAcFeatures
from custom_components.toshiba_ac_community.toshiba_ac.device.properties import ToshibaAcSwingMode
from custom_components.toshiba_ac_community.toshiba_ac.utils.amqp_api import ToshibaAcAmqpApi
from custom_components.toshiba_ac_community.toshiba_ac.utils.http_api import ToshibaAcHttpApi

# Real merit feature strings from the field (library PR #1 thread and
# KaSroka/Toshiba-AC-control#69): the Daiseikai 10 supports H.DA, the Haori
# does not, yet both report bit 14 (Fixed 1-5) - so H.DA must not be derived
# from merit bits at all.
DAISEIKAI_10 = "7c03"
HAORI = "7c02"

# Swing mode is byte 4 of the state hexstring: 0x31 = off, 0x60 = H.DA.
STATE_SWING_OFF = "31421a41313200101a7ffe0200001002000000"
STATE_SWING_HADA = "31421a41603200101a7ffe0200001002000000"


def make_device(merit_feature: str, initial_state: str) -> ToshibaAcDevice:
    return ToshibaAcDevice(
        name="Test AC",
        device_id="device",
        ac_id="ac",
        ac_unique_id="unique",
        initial_ac_state=initial_state,
        firmware_version="1.0.0",
        merit_feature=merit_feature,
        ac_model_id="3",
        amqp_api=t.cast(ToshibaAcAmqpApi, None),
        http_api=t.cast(ToshibaAcHttpApi, None),
    )


class HadaFeatureBitsTest(unittest.TestCase):
    def test_merit_bits_never_advertise_hada(self) -> None:
        for merit in (DAISEIKAI_10, HAORI, "7c0f", "7c1f", "6c03"):
            with self.subTest(merit=merit):
                features = ToshibaAcFeatures.from_merit_string_and_model(merit, "3")
                self.assertNotIn(ToshibaAcSwingMode.HADA, features.ac_swing_mode)
                self.assertIn(ToshibaAcSwingMode.FIXED_5, features.ac_swing_mode)


class HadaLearnOnObserveTest(unittest.TestCase):
    def test_not_supported_until_observed(self) -> None:
        device = make_device(HAORI, STATE_SWING_OFF)
        self.assertNotIn(ToshibaAcSwingMode.HADA, device.supported.ac_swing_mode)

    def test_learned_from_initial_state(self) -> None:
        device = make_device(DAISEIKAI_10, STATE_SWING_HADA)
        self.assertIn(ToshibaAcSwingMode.HADA, device.supported.ac_swing_mode)

    def test_learned_from_state_update(self) -> None:
        device = make_device(DAISEIKAI_10, STATE_SWING_OFF)
        self.assertNotIn(ToshibaAcSwingMode.HADA, device.supported.ac_swing_mode)
        device.fcu_state.update(STATE_SWING_HADA)
        self.assertIn(ToshibaAcSwingMode.HADA, device.supported.ac_swing_mode)

    def test_sticky_after_leaving_hada(self) -> None:
        device = make_device(DAISEIKAI_10, STATE_SWING_HADA)
        self.assertIn(ToshibaAcSwingMode.HADA, device.supported.ac_swing_mode)
        device.fcu_state.update(STATE_SWING_OFF)
        self.assertIn(ToshibaAcSwingMode.HADA, device.supported.ac_swing_mode)


if __name__ == "__main__":
    unittest.main()
