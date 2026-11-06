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

from ..device.properties import EstiaCompressorStatus, EstiaWaterMode, EstiaWaterTemperatures

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

    DECODED_BYTES = 26
    DECODING_STRUCT = struct.Struct(f"{DECODED_BYTES}B")

    class Temperature:
        """Setpoints and the outdoor reading carried in the status string."""

        @staticmethod
        def from_raw(raw: int) -> t.Optional[float]:
            if raw == ToshibaEstiaFcuState.NONE_VAL:
                return None

            return (raw - 32) / 2

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
        self._heating_is_active = 0
        self._water_operation_mode = 0
        self._zone1_target_temperature = self.NONE_VAL
        self._outdoor_unit_heat = 0
        self._heating_coil_heat = 0
        self._outdoor_temperature = self.NONE_VAL
        self._water_pump_status = 0
        self._water_temperatures = EstiaWaterTemperatures()

    @property
    def status_string(self) -> str:
        return self._status_string

    def decode(self, hex_state: str) -> None:
        data = self.DECODING_STRUCT.unpack(bytes.fromhex(hex_state[: self.DECODED_BYTES * 2]))
        (
            self._dhw_is_enabled,  # Byte 1 - DHW function enabled
            self._dhw_target_temperature,  # Byte 2 - DHW target temperature
            self._outdoor_unit_dhw,  # Byte 3 - Outdoor unit active for DHW
            self._heating_coil_dhw,  # Byte 4 - Heater coil active for DHW
            self._heating_is_active,  # Byte 5 - Water function activated
            self._water_operation_mode,  # Byte 6 - Operation mode - heat/cool/auto
            self._zone1_target_temperature,  # Byte 7 - Zone 1 heating target temperature
            _,  # Byte 8 - Superseded zone 1 target temperature
            self._outdoor_unit_heat,  # Byte 9 - Outdoor unit active for heating
            self._heating_coil_heat,  # Byte 10 - Heating coil active for heating
            self._outdoor_temperature,  # Byte 11 - Outdoor temperature
            _,  # Bytes 12-19 - Not understood yet
            _,
            _,
            _,
            _,
            _,
            _,
            _,
            self._water_pump_status,  # Byte 20 - Water pump running
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
        return bool(self._dhw_is_enabled) and self._dhw_is_enabled != self.NONE_VAL

    @property
    def dhw_target_temperature(self) -> t.Optional[float]:
        return ToshibaEstiaFcuState.Temperature.from_raw(self._dhw_target_temperature)

    @property
    def zone1_target_temperature(self) -> t.Optional[float]:
        return ToshibaEstiaFcuState.Temperature.from_raw(self._zone1_target_temperature)

    @property
    def outdoor_temperature(self) -> t.Optional[float]:
        return ToshibaEstiaFcuState.Temperature.from_raw(self._outdoor_temperature)

    @property
    def zone1_mode(self) -> EstiaWaterMode:
        return ToshibaEstiaFcuState.WaterMode.from_raw(self._water_operation_mode)

    @property
    def heating_is_active(self) -> bool:
        return bool(self._heating_is_active)

    @property
    def compressor_status(self) -> EstiaCompressorStatus:
        if self._outdoor_unit_dhw:
            return EstiaCompressorStatus.DHW

        if self._outdoor_unit_heat:
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

    def __str__(self) -> str:
        return (
            f"WaterMode: {self.zone1_mode.name}"
            f", Compressor: {self.compressor_status.name}"
            f", WaterPump: {self.water_pump_is_running}"
            f", ElectricCoil(DHW/Heat): {self.electric_coil_dhw_is_active}/{self.electric_coil_heat_is_active}"
            f", Target(DHW/Zone1): {self.dhw_target_temperature}/{self.zone1_target_temperature}"
        )
