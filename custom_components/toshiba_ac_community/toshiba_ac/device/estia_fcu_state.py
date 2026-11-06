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
    EstiaCompressorStatus,
    EstiaFunctionFlag,
    EstiaWaterMode,
    EstiaWaterTemperatures,
)

logger = logging.getLogger(__name__)


class ToshibaEstiaFcuState:
    """Decoded state of an Estia air-to-water heat pump.

    The wire format is the same hex blob the AC units use, but with an entirely
    different byte layout, so this is a separate class rather than a subclass of
    ToshibaAcFcuState.

    Only the leading bytes are understood. Everything past them is preserved
    verbatim in the raw status string so that merging a partial update never
    drops bytes we cannot interpret yet.
    """

    NONE_VAL = 0xFF

    # Function flags status byte 19 is known to carry (command byte 10 without AUTO)
    STATUS_FUNCTION_FLAGS = int(
        EstiaFunctionFlag.FROST_PROTECTION
        | EstiaFunctionFlag.NIGHT_SETBACK
        | EstiaFunctionFlag.DHW_BOOST
        | EstiaFunctionFlag.ANTI_BACTERIAL
    )

    DECODED_BYTES = 26
    DECODING_STRUCT = struct.Struct(f"{DECODED_BYTES}B")

    class Temperature:
        """Setpoints carried in the status string and in commands, T * 2 + 32."""

        @staticmethod
        def from_raw(raw: int) -> t.Optional[float]:
            if raw == ToshibaEstiaFcuState.NONE_VAL:
                return None

            return (raw - 32) / 2

        @staticmethod
        def to_raw(value: float) -> int:
            raw = int(round(value * 2)) + 32

            # 0xFF would read as "leave unchanged" in a command
            if not 0 <= raw < ToshibaEstiaFcuState.NONE_VAL:
                raise ValueError(f"temperature {value} cannot be encoded")

            return raw

    class WaterTemperature:
        """Water circuit sensors, which use a different offset than the setpoints."""

        @staticmethod
        def from_raw(raw: t.Optional[int]) -> t.Optional[float]:
            # A sensor that is not wired up, or not yet read, reports 0 rather than
            # a NONE marker; a real 0 raw value would mean -24 C at the inlet of a
            # heating circuit, which does not happen.
            if not raw or raw == 0xFF:
                return None

            return (raw - 48) / 2

    class WaterMode:
        RAW_TO_MODE = {
            0x0: EstiaWaterMode.NONE,
            0x5: EstiaWaterMode.COOL,
            0x6: EstiaWaterMode.HEAT,
            # 0xFF is the "no value" marker; spelled literally because the enclosing
            # class is not bound yet while this nested class body runs
            0xFF: EstiaWaterMode.NONE,
            # AUTO has not been observed on the wire yet
        }

        @staticmethod
        def from_raw(raw: int) -> EstiaWaterMode:
            try:
                return ToshibaEstiaFcuState.WaterMode.RAW_TO_MODE[raw]
            except KeyError:
                logger.warning(f"Unknown water operation mode value 0x{raw:02x}, reporting {EstiaWaterMode.NONE}")
                return EstiaWaterMode.NONE

    @classmethod
    def from_hex_state(cls, hex_state: str) -> ToshibaEstiaFcuState:
        state = cls()
        state.update(hex_state)
        return state

    def __init__(self) -> None:
        self._status_string = ""

        self._dhw_is_enabled = self.NONE_VAL
        self._dhw_target_temperature = self.NONE_VAL
        self._outdoor_unit_dhw = 0
        self._heating_coil_dhw = 0
        self._zone1_on_off = self.NONE_VAL
        self._water_operation_mode = 0
        self._zone1_target_temperature = self.NONE_VAL
        self._outdoor_unit_heat = 0
        self._heating_coil_heat = 0
        self._zone1_auto = self.NONE_VAL
        self._zone2_on_off = self.NONE_VAL
        self._zone2_operation_mode = 0
        self._zone2_target_temperature = self.NONE_VAL
        self._zone2_outdoor_unit_heat = 0
        self._zone2_heating_coil_heat = 0
        self._zone2_auto = self.NONE_VAL
        self._function_flags = self.NONE_VAL
        self._water_pump_status = 0
        self._water_temperatures = EstiaWaterTemperatures()

    @property
    def status_string(self) -> str:
        return self._status_string

    def decode(self, hex_state: str) -> None:
        data = self.DECODING_STRUCT.unpack(bytes.fromhex(hex_state[: self.DECODED_BYTES * 2]))
        (
            self._dhw_is_enabled,  # Byte 1 - DHW flags, 0x0c on / 0x08 off
            self._dhw_target_temperature,  # Byte 2 - DHW target temperature
            self._outdoor_unit_dhw,  # Byte 3 - Outdoor unit active for DHW
            self._heating_coil_dhw,  # Byte 4 - Heater coil active for DHW
            self._zone1_on_off,  # Byte 5 - Zone 1 on/off, 0x03 on / 0x02 off
            self._water_operation_mode,  # Byte 6 - Operation mode - heat/cool/auto
            self._zone1_target_temperature,  # Byte 7 - Zone 1 heating target temperature
            _,  # Byte 8 - Second zone 1 target temperature, meaning unknown
            self._outdoor_unit_heat,  # Byte 9 - Outdoor unit active for zone 1
            self._heating_coil_heat,  # Byte 10 - Heating coil active for zone 1
            self._zone1_auto,  # Byte 11 - Zone 1 auto (heating curve) flag
            self._zone2_on_off,  # Byte 12 - Zone 2 on/off, same values as byte 5
            self._zone2_operation_mode,  # Byte 13 - Zone 2 operation mode
            self._zone2_target_temperature,  # Byte 14 - Zone 2 heating target temperature
            _,  # Byte 15 - Second zone 2 target temperature, meaning unknown
            self._zone2_outdoor_unit_heat,  # Byte 16 - Outdoor unit active for zone 2
            self._zone2_heating_coil_heat,  # Byte 17 - Heating coil active for zone 2
            self._zone2_auto,  # Byte 18 - Zone 2 auto (heating curve) flag
            self._function_flags,  # Byte 19 - Function flags, command byte 10 without AUTO
            # Byte 20 - Named "water pump running" by the original decoder, but it
            # was seen going 06 -> 04 when heating was switched off and 04 while
            # heating stayed on, so it is more likely an operating-state field.
            # Kept as is until there is evidence of what it really means.
            self._water_pump_status,
            room_water,  # Bytes 21-26 - Water circuit sensors, same order and
            twi,  # encoding as the fields of the AMQP heartbeat
            two,
            tho,
            to,
            tfi,
        ) = data

        self._water_temperatures = EstiaWaterTemperatures(
            room_water=room_water, twi=twi, two=two, tho=tho, to=to, tfi=tfi
        )

    def merge(self, status_diff: str) -> str:
        """Overlay a partial status string onto the current one.

        The heat pump sends only the bytes that changed and fills the rest with
        0xFF, so an 0xFF byte means "keep what you have" rather than "no value".
        """
        diff_bytes = [status_diff[i : i + 2] for i in range(0, len(status_diff), 2)]
        current_bytes = [self._status_string[i : i + 2] for i in range(0, len(self._status_string), 2)]

        merged = []

        for index, diff_byte in enumerate(diff_bytes):
            keep_current = diff_byte.lower() == "ff" and index < len(current_bytes)
            merged.append(current_bytes[index] if keep_current else diff_byte)

        # A diff shorter than the state we hold must not truncate the rest of it
        merged.extend(current_bytes[len(diff_bytes) :])

        return "".join(merged)

    def update(self, status_diff: str) -> bool:
        merged = self.merge(status_diff)

        if merged == self._status_string:
            return False

        if len(merged) < self.DECODED_BYTES * 2:
            logger.warning(f"Ignoring Estia state, expected at least {self.DECODED_BYTES} bytes, got: {merged}")
            return False

        try:
            self.decode(merged)
        except (struct.error, ValueError) as e:
            logger.warning(f"Ignoring malformed Estia state '{merged}': {e}")
            return False

        self._status_string = merged

        return True

    @property
    def water_temperatures(self) -> EstiaWaterTemperatures:
        """Return the raw water circuit readings carried in the status string."""
        return self._water_temperatures

    @property
    def dhw_is_enabled(self) -> bool:
        # 0x0c on, 0x08 off: bit 0x04 is the enable bit
        return self._dhw_is_enabled != self.NONE_VAL and bool(self._dhw_is_enabled & 0x04)

    @property
    def dhw_target_temperature(self) -> t.Optional[float]:
        return ToshibaEstiaFcuState.Temperature.from_raw(self._dhw_target_temperature)

    @property
    def zone1_target_temperature(self) -> t.Optional[float]:
        return ToshibaEstiaFcuState.Temperature.from_raw(self._zone1_target_temperature)

    @property
    def zone2_target_temperature(self) -> t.Optional[float]:
        return ToshibaEstiaFcuState.Temperature.from_raw(self._zone2_target_temperature)

    @property
    def zone1_mode(self) -> EstiaWaterMode:
        return ToshibaEstiaFcuState.WaterMode.from_raw(self._water_operation_mode)

    @property
    def zone2_mode(self) -> EstiaWaterMode:
        return ToshibaEstiaFcuState.WaterMode.from_raw(self._zone2_operation_mode)

    @property
    def zone1_is_on(self) -> bool:
        return self._is_on(self._zone1_on_off)

    @property
    def zone2_is_on(self) -> bool:
        return self._is_on(self._zone2_on_off)

    @property
    def heating_is_active(self) -> bool:
        """Kept for compatibility; the same as zone1_is_on."""
        return self.zone1_is_on

    @property
    def has_zone2(self) -> bool:
        """Return True if the unit reports a second heating zone."""
        return self._zone2_on_off not in (0x00, self.NONE_VAL)

    @property
    def zone1_auto(self) -> t.Optional[bool]:
        """Return the zone 1 auto (heating curve) flag, None if not reported."""
        return self._flag(self._zone1_auto)

    @property
    def zone2_auto(self) -> t.Optional[bool]:
        """Return the zone 2 auto (heating curve) flag, None if not reported."""
        return self._flag(self._zone2_auto)

    @property
    def function_flags(self) -> int:
        """Return status byte 19 as is: 0xFF when the unit has not reported it."""
        return self._function_flags

    def has_function_flag(self, flag: EstiaFunctionFlag) -> bool:
        if self._function_flags == self.NONE_VAL:
            return False

        return bool(self._function_flags & int(flag))

    def function_register(self) -> int:
        """Rebuild command byte 10 from the current state.

        The unit rewrites the whole register on every command, so the value sent
        must carry every function exactly as it is now: the function flags from
        status byte 19 plus the auto flag from status byte 11.

        Raises ValueError rather than guess if the state holds anything that
        cannot be re-sent faithfully: a flag register or auto flag that was never
        reported, or bits whose meaning is unknown. Sending unknown bits could
        switch on something nobody understands, and dropping them would silently
        switch it off, so neither is done.
        """
        flags = self._function_flags

        if flags == self.NONE_VAL:
            raise ValueError("the function flags have not been reported yet")

        unknown = flags & (0xFF ^ self.STATUS_FUNCTION_FLAGS)
        if unknown:
            raise ValueError(f"the function flags hold unknown bits 0x{unknown:02x} (status byte 19 = 0x{flags:02x})")

        auto = self.zone1_auto
        if auto is None:
            raise ValueError(f"the auto flag has an unknown value 0x{self._zone1_auto:02x}")

        return flags | (int(EstiaFunctionFlag.AUTO) if auto else 0)

    def _is_on(self, raw: int) -> bool:
        return raw not in (0x00, self.NONE_VAL) and bool(raw & 0x01)

    def _flag(self, raw: int) -> t.Optional[bool]:
        if raw in (0x00, 0x01):
            return bool(raw)

        return None

    @property
    def compressor_status(self) -> EstiaCompressorStatus:
        if self._outdoor_unit_dhw:
            return EstiaCompressorStatus.DHW

        if self.zone1_outdoor_unit_is_active or self.zone2_outdoor_unit_is_active:
            return EstiaCompressorStatus.HEAT

        return EstiaCompressorStatus.OFF

    @property
    def water_pump_is_running(self) -> bool:
        return bool(self._water_pump_status)

    @property
    def electric_coil_dhw_is_active(self) -> bool:
        return bool(self._heating_coil_dhw)

    @property
    def electric_coil_heat_is_active(self) -> bool:
        return bool(self._heating_coil_heat)

    @property
    def zone1_outdoor_unit_is_active(self) -> bool:
        return bool(self._outdoor_unit_heat)

    @property
    def zone2_outdoor_unit_is_active(self) -> bool:
        return self._zone2_outdoor_unit_heat not in (0x00, self.NONE_VAL)

    @property
    def zone2_electric_coil_is_active(self) -> bool:
        return self._zone2_heating_coil_heat not in (0x00, self.NONE_VAL)

    def __str__(self) -> str:
        return (
            f"WaterMode: {self.zone1_mode.name}"
            f", Compressor: {self.compressor_status.name}"
            f", WaterPump: {self.water_pump_is_running}"
            f", ElectricCoil(DHW/Heat): {self.electric_coil_dhw_is_active}/{self.electric_coil_heat_is_active}"
            f", Target(DHW/Zone1/Zone2): {self.dhw_target_temperature}/{self.zone1_target_temperature}"
            f"/{self.zone2_target_temperature}"
            f", On(DHW/Zone1/Zone2): {self.dhw_is_enabled}/{self.zone1_is_on}/{self.zone2_is_on}"
            f", Auto: {self.zone1_auto}, Functions: 0x{self.function_flags:02x}"
        )
