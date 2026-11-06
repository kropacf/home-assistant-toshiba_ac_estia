# Copyright 2021 Kamil Sroka

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import logging
import struct
import typing as t

from ..device.properties import (
    ToshibaAcAirPureIon,
    ToshibaAcFanMode,
    ToshibaAcMeritA,
    ToshibaAcMeritB,
    ToshibaAcMode,
    ToshibaAcPowerSelection,
    ToshibaAcSelfCleaning,
    ToshibaAcWirelessLed,
    ToshibaAcStatus,
    ToshibaAcHorizontalSwingMode,
    ToshibaAcSwingMode,
    ToshibaAcVerticalSwingMode,
)

logger = logging.getLogger(__name__)

_Property = t.TypeVar("_Property")


def _decode(field: str, mapping: t.Dict[int, _Property], raw: int, unknown: _Property) -> _Property:
    # Toshiba keeps adding raw values for new hardware, and a KeyError here escapes
    # through every state update and every read of the decoded property, which bricks
    # the consumer until the AC happens to report a known value again. An unrecognised
    # byte must degrade to "unknown", never take the connection down with it.
    try:
        return mapping[raw]
    except KeyError:
        logger.warning(f"Unknown {field} value 0x{raw:02x}, reporting {unknown}")
        return unknown


class ToshibaAcFcuState:
    NONE_VAL = 0xFF
    NONE_VAL_HALF = 0x0F
    NONE_VAL_SIGNED = -1
    ENCODING_STRUCT = struct.Struct("BBbBBBBBBbbBBBBBBBBB")

    class AcTemperature:
        @staticmethod
        def from_raw(raw: int) -> t.Optional[int]:
            raw_to_temp: t.Dict[int, t.Optional[int]] = {i: i for i in range(-128, 128)}
            raw_to_temp.update({127: None, -128: None, ToshibaAcFcuState.NONE_VAL_SIGNED: None, 126: -1})
            return raw_to_temp[raw]

        @staticmethod
        def to_raw(temperature: t.Optional[t.Union[int, float]]) -> int:
            # Callers legitimately supply floats: Home Assistant's climate platform
            # coerces the temperature attribute to float, external thermostats compute
            # fractional setpoints, and float arithmetic yields values such as
            # 21.999999999999986 for what is meant to be 22. The wire format encodes
            # temperature as a single signed byte, so only whole degrees can be
            # represented -- round to the nearest one instead of raising KeyError.
            #
            # Rounding is half-up (half away from zero for negatives) rather than
            # round(), which uses banker's rounding: it sends an exact .5 to the
            # nearest *even* integer, so 21.5 -> 22 but 22.5 -> 22, and 23.5 -> 24 but
            # 24.5 -> 24. That avoids upward bias when summing many values, which is
            # irrelevant for a one-shot setpoint and reads as inconsistent to a user
            # asking for half a degree. Half-up keeps every .5 going the same way.
            if temperature is not None and not isinstance(temperature, int):
                temperature = int(temperature + 0.5) if temperature >= 0 else -int(-temperature + 0.5)

            temp_to_raw: t.Dict[t.Optional[int], int] = {i: i for i in range(-128, 128)}
            temp_to_raw.update({None: ToshibaAcFcuState.NONE_VAL_SIGNED, -1: 126})
            return temp_to_raw[temperature]

    class AcStatus:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcStatus:
            return _decode(
                "AcStatus",
                {
                    0x30: ToshibaAcStatus.ON,
                    0x31: ToshibaAcStatus.OFF,
                    0x02: ToshibaAcStatus.NONE,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcStatus.NONE,
                },
                raw,
                ToshibaAcStatus.NONE,
            )

        @staticmethod
        def to_raw(status: ToshibaAcStatus) -> int:
            return {
                ToshibaAcStatus.ON: 0x30,
                ToshibaAcStatus.OFF: 0x31,
                ToshibaAcStatus.NONE: ToshibaAcFcuState.NONE_VAL,
            }[status]

    class AcMode:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcMode:
            return _decode(
                "AcMode",
                {
                    0x41: ToshibaAcMode.AUTO,
                    0x42: ToshibaAcMode.COOL,
                    0x43: ToshibaAcMode.HEAT,
                    0x44: ToshibaAcMode.DRY,
                    0x45: ToshibaAcMode.FAN,
                    0x00: ToshibaAcMode.NONE,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcMode.NONE,
                },
                raw,
                ToshibaAcMode.NONE,
            )

        @staticmethod
        def to_raw(mode: ToshibaAcMode) -> int:
            return {
                ToshibaAcMode.AUTO: 0x41,
                ToshibaAcMode.COOL: 0x42,
                ToshibaAcMode.HEAT: 0x43,
                ToshibaAcMode.DRY: 0x44,
                ToshibaAcMode.FAN: 0x45,
                ToshibaAcMode.NONE: ToshibaAcFcuState.NONE_VAL,
            }[mode]

    class AcFanMode:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcFanMode:
            return _decode(
                "AcFanMode",
                {
                    0x41: ToshibaAcFanMode.AUTO,
                    0x31: ToshibaAcFanMode.QUIET,
                    0x32: ToshibaAcFanMode.LOW,
                    0x33: ToshibaAcFanMode.MEDIUM_LOW,
                    0x34: ToshibaAcFanMode.MEDIUM,
                    0x35: ToshibaAcFanMode.MEDIUM_HIGH,
                    0x36: ToshibaAcFanMode.HIGH,
                    0x00: ToshibaAcFanMode.NONE,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcFanMode.NONE,
                },
                raw,
                ToshibaAcFanMode.NONE,
            )

        @staticmethod
        def to_raw(fan_mode: ToshibaAcFanMode) -> int:
            return {
                ToshibaAcFanMode.AUTO: 0x41,
                ToshibaAcFanMode.QUIET: 0x31,
                ToshibaAcFanMode.LOW: 0x32,
                ToshibaAcFanMode.MEDIUM_LOW: 0x33,
                ToshibaAcFanMode.MEDIUM: 0x34,
                ToshibaAcFanMode.MEDIUM_HIGH: 0x35,
                ToshibaAcFanMode.HIGH: 0x36,
                ToshibaAcFanMode.NONE: ToshibaAcFcuState.NONE_VAL,
            }[fan_mode]

    class AcSwingMode:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcSwingMode:
            # 2026 units (Shorai Curve) report the two vanes independently in one byte
            # instead of picking a preset: 0x80 | horizontal << 3 | vertical, where each
            # axis is 0 for none, 1-5 for fixed positions 1-5 and 6 for swinging. They
            # still accept the legacy preset commands, so only decoding is needed here.
            # ponytail: collapsed onto the legacy enum, which cannot express a fixed
            # horizontal position - per-axis modes are Toshiba-AC-control#2.
            vertical, horizontal = raw & 0x07, (raw & 0x38) >> 3
            if 0x80 <= raw <= 0xB6 and vertical <= 0x06 and horizontal <= 0x06:
                if vertical == 0x06:
                    return (
                        ToshibaAcSwingMode.SWING_VERTICAL_AND_HORIZONTAL
                        if horizontal == 0x06
                        else ToshibaAcSwingMode.SWING_VERTICAL
                    )
                if horizontal == 0x06:
                    return ToshibaAcSwingMode.SWING_HORIZONTAL
                if vertical:
                    return [
                        ToshibaAcSwingMode.FIXED_1,
                        ToshibaAcSwingMode.FIXED_2,
                        ToshibaAcSwingMode.FIXED_3,
                        ToshibaAcSwingMode.FIXED_4,
                        ToshibaAcSwingMode.FIXED_5,
                    ][vertical - 1]
                return ToshibaAcSwingMode.OFF

            return _decode(
                "AcSwingMode",
                {
                    0x31: ToshibaAcSwingMode.OFF,
                    0x41: ToshibaAcSwingMode.SWING_VERTICAL,
                    0x42: ToshibaAcSwingMode.SWING_HORIZONTAL,
                    0x43: ToshibaAcSwingMode.SWING_VERTICAL_AND_HORIZONTAL,
                    0x50: ToshibaAcSwingMode.FIXED_1,
                    0x51: ToshibaAcSwingMode.FIXED_2,
                    0x52: ToshibaAcSwingMode.FIXED_3,
                    0x53: ToshibaAcSwingMode.FIXED_4,
                    0x54: ToshibaAcSwingMode.FIXED_5,
                    0x60: ToshibaAcSwingMode.HADA,
                    0x00: ToshibaAcSwingMode.NONE,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcSwingMode.NONE,
                },
                raw,
                ToshibaAcSwingMode.NONE,
            )

        @staticmethod
        def to_raw(swing_mode: ToshibaAcSwingMode) -> int:
            return {
                ToshibaAcSwingMode.OFF: 0x31,
                ToshibaAcSwingMode.SWING_VERTICAL: 0x41,
                ToshibaAcSwingMode.SWING_HORIZONTAL: 0x42,
                ToshibaAcSwingMode.SWING_VERTICAL_AND_HORIZONTAL: 0x43,
                ToshibaAcSwingMode.FIXED_1: 0x50,
                ToshibaAcSwingMode.FIXED_2: 0x51,
                ToshibaAcSwingMode.FIXED_3: 0x52,
                ToshibaAcSwingMode.FIXED_4: 0x53,
                ToshibaAcSwingMode.FIXED_5: 0x54,
                ToshibaAcSwingMode.HADA: 0x60,
                ToshibaAcSwingMode.NONE: ToshibaAcFcuState.NONE_VAL,
            }[swing_mode]

    class AcIndependentSwingMode:
        # 2026 units (Shorai Curve) encode both vanes in one byte:
        # 0x80 | horizontal << 3 | vertical, each axis 0 none, 1-5 fixed, 6 swing.
        # See Toshiba-AC-control#2 for the reverse-engineered mapping.
        _AXIS_VERTICAL = [
            ToshibaAcVerticalSwingMode.NONE,
            ToshibaAcVerticalSwingMode.FIXED_1,
            ToshibaAcVerticalSwingMode.FIXED_2,
            ToshibaAcVerticalSwingMode.FIXED_3,
            ToshibaAcVerticalSwingMode.FIXED_4,
            ToshibaAcVerticalSwingMode.FIXED_5,
            ToshibaAcVerticalSwingMode.SWING,
        ]
        _AXIS_HORIZONTAL = [
            ToshibaAcHorizontalSwingMode.NONE,
            ToshibaAcHorizontalSwingMode.FIXED_1,
            ToshibaAcHorizontalSwingMode.FIXED_2,
            ToshibaAcHorizontalSwingMode.FIXED_3,
            ToshibaAcHorizontalSwingMode.FIXED_4,
            ToshibaAcHorizontalSwingMode.FIXED_5,
            ToshibaAcHorizontalSwingMode.SWING,
        ]
        # Best-effort reading of legacy preset bytes as axis positions, so the
        # per-axis properties are meaningful on every unit and after legacy sends.
        _LEGACY_VERTICAL = {
            0x41: ToshibaAcVerticalSwingMode.SWING,
            0x43: ToshibaAcVerticalSwingMode.SWING,
            0x50: ToshibaAcVerticalSwingMode.FIXED_1,
            0x51: ToshibaAcVerticalSwingMode.FIXED_2,
            0x52: ToshibaAcVerticalSwingMode.FIXED_3,
            0x53: ToshibaAcVerticalSwingMode.FIXED_4,
            0x54: ToshibaAcVerticalSwingMode.FIXED_5,
        }
        _LEGACY_HORIZONTAL = {
            0x42: ToshibaAcHorizontalSwingMode.SWING,
            0x43: ToshibaAcHorizontalSwingMode.SWING,
        }

        @staticmethod
        def is_independent_raw(raw: int) -> bool:
            return 0x80 <= raw <= 0xB6 and raw & 0x07 <= 0x06 and (raw & 0x38) >> 3 <= 0x06

        @staticmethod
        def vertical_from_raw(raw: int) -> ToshibaAcVerticalSwingMode:
            codec = ToshibaAcFcuState.AcIndependentSwingMode
            if codec.is_independent_raw(raw):
                return codec._AXIS_VERTICAL[raw & 0x07]
            return codec._LEGACY_VERTICAL.get(raw, ToshibaAcVerticalSwingMode.NONE)

        @staticmethod
        def horizontal_from_raw(raw: int) -> ToshibaAcHorizontalSwingMode:
            codec = ToshibaAcFcuState.AcIndependentSwingMode
            if codec.is_independent_raw(raw):
                return codec._AXIS_HORIZONTAL[(raw & 0x38) >> 3]
            return codec._LEGACY_HORIZONTAL.get(raw, ToshibaAcHorizontalSwingMode.NONE)

        @staticmethod
        def to_raw(vertical: ToshibaAcVerticalSwingMode, horizontal: ToshibaAcHorizontalSwingMode) -> int:
            codec = ToshibaAcFcuState.AcIndependentSwingMode
            return 0x80 | codec._AXIS_HORIZONTAL.index(horizontal) << 3 | codec._AXIS_VERTICAL.index(vertical)

    class AcPowerSelection:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcPowerSelection:
            return _decode(
                "AcPowerSelection",
                {
                    0x32: ToshibaAcPowerSelection.POWER_50,
                    0x4B: ToshibaAcPowerSelection.POWER_75,
                    0x64: ToshibaAcPowerSelection.POWER_100,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcPowerSelection.NONE,
                },
                raw,
                ToshibaAcPowerSelection.NONE,
            )

        @staticmethod
        def to_raw(power_selection: ToshibaAcPowerSelection) -> int:
            return {
                ToshibaAcPowerSelection.POWER_50: 0x32,
                ToshibaAcPowerSelection.POWER_75: 0x4B,
                ToshibaAcPowerSelection.POWER_100: 0x64,
                ToshibaAcPowerSelection.NONE: ToshibaAcFcuState.NONE_VAL,
            }[power_selection]

    class AcMeritB:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcMeritB:
            return _decode(
                "AcMeritB",
                {
                    0x02: ToshibaAcMeritB.FIREPLACE_1,
                    0x03: ToshibaAcMeritB.FIREPLACE_2,
                    0x01: ToshibaAcMeritB.OFF,  # New value reported after update, nothing found in 3.4.0 APK version
                    0x00: ToshibaAcMeritB.OFF,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcMeritB.NONE,
                    ToshibaAcFcuState.NONE_VAL_HALF: ToshibaAcMeritB.NONE,
                },
                raw,
                ToshibaAcMeritB.NONE,
            )

        @staticmethod
        def to_raw(merit_b: ToshibaAcMeritB) -> int:
            return {
                ToshibaAcMeritB.FIREPLACE_1: 0x02,
                ToshibaAcMeritB.FIREPLACE_2: 0x03,
                ToshibaAcMeritB.OFF: 0x00,
                ToshibaAcMeritB.NONE: ToshibaAcFcuState.NONE_VAL,
            }[merit_b]

    class AcMeritA:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcMeritA:
            return _decode(
                "AcMeritA",
                {
                    0x01: ToshibaAcMeritA.HIGH_POWER,
                    0x02: ToshibaAcMeritA.CDU_SILENT_1,
                    0x03: ToshibaAcMeritA.ECO,
                    0x04: ToshibaAcMeritA.HEATING_8C,
                    0x05: ToshibaAcMeritA.SLEEP_CARE,
                    0x06: ToshibaAcMeritA.FLOOR,
                    0x07: ToshibaAcMeritA.COMFORT,
                    0x0A: ToshibaAcMeritA.CDU_SILENT_2,
                    0x00: ToshibaAcMeritA.OFF,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcMeritA.NONE,
                    ToshibaAcFcuState.NONE_VAL_HALF: ToshibaAcMeritA.NONE,
                },
                raw,
                ToshibaAcMeritA.NONE,
            )

        @staticmethod
        def to_raw(merit_a: ToshibaAcMeritA) -> int:
            return {
                ToshibaAcMeritA.HIGH_POWER: 0x01,
                ToshibaAcMeritA.CDU_SILENT_1: 0x02,
                ToshibaAcMeritA.ECO: 0x03,
                ToshibaAcMeritA.HEATING_8C: 0x04,
                ToshibaAcMeritA.SLEEP_CARE: 0x05,
                ToshibaAcMeritA.FLOOR: 0x06,
                ToshibaAcMeritA.COMFORT: 0x07,
                ToshibaAcMeritA.CDU_SILENT_2: 0x0A,
                ToshibaAcMeritA.OFF: 0x00,
                ToshibaAcMeritA.NONE: ToshibaAcFcuState.NONE_VAL,
            }[merit_a]

    class AcAirPureIon:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcAirPureIon:
            return _decode(
                "AcAirPureIon",
                {
                    0x18: ToshibaAcAirPureIon.ON,
                    0x10: ToshibaAcAirPureIon.OFF,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcAirPureIon.NONE,
                },
                raw,
                ToshibaAcAirPureIon.NONE,
            )

        @staticmethod
        def to_raw(air_pure_ion: ToshibaAcAirPureIon) -> int:
            return {
                ToshibaAcAirPureIon.ON: 0x18,
                ToshibaAcAirPureIon.OFF: 0x10,
                ToshibaAcAirPureIon.NONE: ToshibaAcFcuState.NONE_VAL,
            }[air_pure_ion]

    class AcSelfCleaning:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcSelfCleaning:
            return _decode(
                "AcSelfCleaning",
                {
                    0x18: ToshibaAcSelfCleaning.ON,
                    0x10: ToshibaAcSelfCleaning.OFF,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcSelfCleaning.NONE,
                },
                raw,
                ToshibaAcSelfCleaning.NONE,
            )

        @staticmethod
        def to_raw(self_cleaning: ToshibaAcSelfCleaning) -> int:
            return {
                ToshibaAcSelfCleaning.ON: 0x18,
                ToshibaAcSelfCleaning.OFF: 0x10,
                ToshibaAcSelfCleaning.NONE: ToshibaAcFcuState.NONE_VAL,
            }[self_cleaning]

    class AcWirelessLed:
        @staticmethod
        def from_raw(raw: int) -> ToshibaAcWirelessLed:
            return _decode(
                "AcWirelessLed",
                {
                    0x01: ToshibaAcWirelessLed.ON,
                    0x02: ToshibaAcWirelessLed.OFF,
                    ToshibaAcFcuState.NONE_VAL: ToshibaAcWirelessLed.NONE,
                },
                raw,
                ToshibaAcWirelessLed.NONE,
            )

        @staticmethod
        def to_raw(wireless_led: ToshibaAcWirelessLed) -> int:
            return {
                ToshibaAcWirelessLed.ON: 0x01,
                ToshibaAcWirelessLed.OFF: 0x02,
                ToshibaAcWirelessLed.NONE: ToshibaAcFcuState.NONE_VAL,
            }[wireless_led]

    @classmethod
    def from_hex_state(cls, hex_state: str) -> ToshibaAcFcuState:
        state = cls()
        state.decode(hex_state)
        return state

    def __init__(self) -> None:
        self._ac_status = ToshibaAcFcuState.NONE_VAL
        self._ac_mode = ToshibaAcFcuState.NONE_VAL
        self._ac_temperature = ToshibaAcFcuState.NONE_VAL_SIGNED
        self._ac_fan_mode = ToshibaAcFcuState.NONE_VAL
        self._ac_swing_mode = ToshibaAcFcuState.NONE_VAL
        self._ac_power_selection = ToshibaAcFcuState.NONE_VAL
        self._ac_merit_b = ToshibaAcFcuState.NONE_VAL
        self._ac_merit_a = ToshibaAcFcuState.NONE_VAL
        self._ac_air_pure_ion = ToshibaAcFcuState.NONE_VAL
        self._ac_indoor_temperature = ToshibaAcFcuState.NONE_VAL_SIGNED
        self._ac_outdoor_temperature = ToshibaAcFcuState.NONE_VAL_SIGNED
        self._ac_self_cleaning = ToshibaAcFcuState.NONE_VAL
        self._ac_wireless_led = ToshibaAcFcuState.NONE_VAL

    def encode(self) -> str:
        encoded = self.ENCODING_STRUCT.pack(
            self._ac_status,
            self._ac_mode,
            self._ac_temperature,
            self._ac_fan_mode,
            self._ac_swing_mode,
            self._ac_power_selection,
            self._ac_merit_b,
            self._ac_merit_a,
            self._ac_air_pure_ion,
            self._ac_indoor_temperature,
            self._ac_outdoor_temperature,
            ToshibaAcFcuState.NONE_VAL,
            ToshibaAcFcuState.NONE_VAL,
            ToshibaAcFcuState.NONE_VAL,
            ToshibaAcFcuState.NONE_VAL,
            self._ac_self_cleaning,
            self._ac_wireless_led,
            ToshibaAcFcuState.NONE_VAL,
            ToshibaAcFcuState.NONE_VAL,
            ToshibaAcFcuState.NONE_VAL,
        ).hex()
        return (
            encoded[:12] + encoded[13] + encoded[15] + encoded[16:]
        )  # Merit A/B features are encoded using half bytes but our packing added them as bytes

    def decode(self, hex_state: str) -> None:
        extended_hex_state = (
            hex_state[:12] + "0" + hex_state[12] + "0" + hex_state[13:38]
        )  # Merit A/B features are encoded using half bytes but our unpacking expect them as bytes.
        # We also ignore any extra bytes if present.
        data = self.ENCODING_STRUCT.unpack(bytes.fromhex(extended_hex_state))
        (
            self._ac_status,
            self._ac_mode,
            self._ac_temperature,
            self._ac_fan_mode,
            self._ac_swing_mode,
            self._ac_power_selection,
            self._ac_merit_b,
            self._ac_merit_a,
            self._ac_air_pure_ion,
            self._ac_indoor_temperature,
            self._ac_outdoor_temperature,
            _,
            _,
            _,
            _,
            self._ac_self_cleaning,
            self._ac_wireless_led,
            *_,
        ) = data

    def update(self, hex_state: str) -> bool:
        state_update = ToshibaAcFcuState.from_hex_state(hex_state)

        changed = False

        enum_states = [
            "_ac_status",
            "_ac_mode",
            "_ac_fan_mode",
            "_ac_swing_mode",
            "_ac_power_selection",
            "_ac_merit_b",
            "_ac_merit_a",
            "_ac_air_pure_ion",
            "_ac_self_cleaning",
            "_ac_wireless_led",
        ]

        temperature_states = [
            "_ac_temperature",
            "_ac_indoor_temperature",
            "_ac_outdoor_temperature",
        ]

        for enum_state in enum_states:
            updated_state = getattr(state_update, enum_state)
            current_state = getattr(self, enum_state)
            if updated_state not in [ToshibaAcFcuState.NONE_VAL, ToshibaAcFcuState.NONE_VAL_HALF, current_state]:
                setattr(self, enum_state, updated_state)
                changed = True

        for temperature_state in temperature_states:
            updated_state = getattr(state_update, temperature_state)
            current_state = getattr(self, temperature_state)
            if updated_state not in [ToshibaAcFcuState.NONE_VAL_SIGNED, current_state]:
                setattr(self, temperature_state, updated_state)
                changed = True

        return changed

    def update_from_hbt(self, hb_data: t.Any) -> bool:
        changed = False

        if "iTemp" in hb_data and hb_data["iTemp"] != self._ac_indoor_temperature:
            self._ac_indoor_temperature = hb_data["iTemp"]
            changed = True

        if "oTemp" in hb_data and hb_data["oTemp"] != self._ac_outdoor_temperature:
            self._ac_outdoor_temperature = hb_data["oTemp"]
            changed = True

        return changed

    @property
    def ac_status(self) -> ToshibaAcStatus:
        return ToshibaAcFcuState.AcStatus.from_raw(self._ac_status)

    @ac_status.setter
    def ac_status(self, val: ToshibaAcStatus) -> None:
        self._ac_status = ToshibaAcFcuState.AcStatus.to_raw(val)

    @property
    def ac_mode(self) -> ToshibaAcMode:
        return ToshibaAcFcuState.AcMode.from_raw(self._ac_mode)

    @ac_mode.setter
    def ac_mode(self, val: ToshibaAcMode) -> None:
        self._ac_mode = ToshibaAcFcuState.AcMode.to_raw(val)

    @property
    def ac_temperature(self) -> t.Optional[int]:
        return ToshibaAcFcuState.AcTemperature.from_raw(self._ac_temperature)

    @ac_temperature.setter
    def ac_temperature(self, val: t.Optional[t.Union[int, float]]) -> None:
        self._ac_temperature = ToshibaAcFcuState.AcTemperature.to_raw(val)

    @property
    def ac_fan_mode(self) -> ToshibaAcFanMode:
        return ToshibaAcFcuState.AcFanMode.from_raw(self._ac_fan_mode)

    @ac_fan_mode.setter
    def ac_fan_mode(self, val: ToshibaAcFanMode) -> None:
        self._ac_fan_mode = ToshibaAcFcuState.AcFanMode.to_raw(val)

    @property
    def ac_swing_mode(self) -> ToshibaAcSwingMode:
        return ToshibaAcFcuState.AcSwingMode.from_raw(self._ac_swing_mode)

    @ac_swing_mode.setter
    def ac_swing_mode(self, val: ToshibaAcSwingMode) -> None:
        self._ac_swing_mode = ToshibaAcFcuState.AcSwingMode.to_raw(val)

    @property
    def uses_independent_swing(self) -> bool:
        return ToshibaAcFcuState.AcIndependentSwingMode.is_independent_raw(self._ac_swing_mode)

    @property
    def ac_vertical_swing_mode(self) -> ToshibaAcVerticalSwingMode:
        return ToshibaAcFcuState.AcIndependentSwingMode.vertical_from_raw(self._ac_swing_mode)

    @property
    def ac_horizontal_swing_mode(self) -> ToshibaAcHorizontalSwingMode:
        return ToshibaAcFcuState.AcIndependentSwingMode.horizontal_from_raw(self._ac_swing_mode)

    def set_ac_independent_swing(
        self, vertical: ToshibaAcVerticalSwingMode, horizontal: ToshibaAcHorizontalSwingMode
    ) -> None:
        self._ac_swing_mode = ToshibaAcFcuState.AcIndependentSwingMode.to_raw(vertical, horizontal)

    @property
    def ac_power_selection(self) -> ToshibaAcPowerSelection:
        return ToshibaAcFcuState.AcPowerSelection.from_raw(self._ac_power_selection)

    @ac_power_selection.setter
    def ac_power_selection(self, val: ToshibaAcPowerSelection) -> None:
        self._ac_power_selection = ToshibaAcFcuState.AcPowerSelection.to_raw(val)

    @property
    def ac_merit_b(self) -> ToshibaAcMeritB:
        return ToshibaAcFcuState.AcMeritB.from_raw(self._ac_merit_b)

    @ac_merit_b.setter
    def ac_merit_b(self, val: ToshibaAcMeritB) -> None:
        self._ac_merit_b = ToshibaAcFcuState.AcMeritB.to_raw(val)

    @property
    def ac_merit_a(self) -> ToshibaAcMeritA:
        return ToshibaAcFcuState.AcMeritA.from_raw(self._ac_merit_a)

    @ac_merit_a.setter
    def ac_merit_a(self, val: ToshibaAcMeritA) -> None:
        self._ac_merit_a = ToshibaAcFcuState.AcMeritA.to_raw(val)

    @property
    def ac_air_pure_ion(self) -> ToshibaAcAirPureIon:
        return ToshibaAcFcuState.AcAirPureIon.from_raw(self._ac_air_pure_ion)

    @ac_air_pure_ion.setter
    def ac_air_pure_ion(self, val: ToshibaAcAirPureIon) -> None:
        self._ac_air_pure_ion = ToshibaAcFcuState.AcAirPureIon.to_raw(val)

    @property
    def ac_indoor_temperature(self) -> t.Optional[int]:
        return ToshibaAcFcuState.AcTemperature.from_raw(self._ac_indoor_temperature)

    @ac_indoor_temperature.setter
    def ac_indoor_temperature(self, val: t.Optional[int]) -> None:
        self._ac_indoor_temperature = ToshibaAcFcuState.AcTemperature.to_raw(val)

    @property
    def ac_outdoor_temperature(self) -> t.Optional[int]:
        return ToshibaAcFcuState.AcTemperature.from_raw(self._ac_outdoor_temperature)

    @ac_outdoor_temperature.setter
    def ac_outdoor_temperature(self, val: t.Optional[int]) -> None:
        self._ac_outdoor_temperature = ToshibaAcFcuState.AcTemperature.to_raw(val)

    @property
    def ac_self_cleaning(self) -> ToshibaAcSelfCleaning:
        return ToshibaAcFcuState.AcSelfCleaning.from_raw(self._ac_self_cleaning)

    @ac_self_cleaning.setter
    def ac_self_cleaning(self, val: ToshibaAcSelfCleaning) -> None:
        self._ac_self_cleaning = ToshibaAcFcuState.AcSelfCleaning.to_raw(val)

    @property
    def ac_wireless_led(self) -> ToshibaAcWirelessLed:
        return ToshibaAcFcuState.AcWirelessLed.from_raw(self._ac_wireless_led)

    @ac_wireless_led.setter
    def ac_wireless_led(self, val: ToshibaAcWirelessLed) -> None:
        self._ac_wireless_led = ToshibaAcFcuState.AcWirelessLed.to_raw(val)

    def __str__(self) -> str:
        res = f"AcStatus: {self.ac_status.name}"
        res += f", AcMode: {self.ac_mode.name}"
        res += f", AcTemperature: {self.ac_temperature}"
        res += f", AcFanMode: {self.ac_fan_mode.name}"
        res += f", AcSwingMode: {self.ac_swing_mode.name}"
        if self.uses_independent_swing:
            res += f" (vertical: {self.ac_vertical_swing_mode.name}, horizontal: {self.ac_horizontal_swing_mode.name})"
        res += f", AcPowerSelection: {self.ac_power_selection.name}"
        res += f", AcFeatureMeritB: {self.ac_merit_b.name}"
        res += f", AcFeatureMeritA: {self.ac_merit_a.name}"
        res += f", AcAirPureIon: {self.ac_air_pure_ion.name}"
        res += f", AcIndoorAcTemperature: {self.ac_indoor_temperature}"
        res += f", AcOutdoorAcTemperature: {self.ac_outdoor_temperature}"
        res += f", AcSelfCleaning: {self.ac_self_cleaning.name}"
        res += f", AcWirelessLed: {self.ac_wireless_led.name}"

        return res
