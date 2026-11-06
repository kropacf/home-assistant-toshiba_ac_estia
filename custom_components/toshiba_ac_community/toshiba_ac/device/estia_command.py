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

import typing as t
from dataclasses import dataclass

from ..device.estia_fcu_state import ToshibaEstiaFcuState


@dataclass
class ToshibaEstiaCommand:
    """Payload of a CMD_HDU_TO_ESTIA command, 14 bytes.

    The command layout is not the status layout. Every field left at None is
    sent as 0xFF, which the unit reads as "leave unchanged". Layout (1-based),
    as captured from the Toshiba app:

      1     DHW on/off, 0x0c on / 0x08 off
      2     DHW target, T * 2 + 32
      3     heating on/off for zones 1 and 2 together, 0x03 on / 0x02 off
      5     zone 1 target, T * 2 + 32
      6     zone 2 target, T * 2 + 32
      10    function register, see EstiaFunctionFlag. The unit rewrites it as a
            whole, so it must be built from the current state
            (ToshibaEstiaFcuState.function_register), never from a single bit.
      4, 7-9, 11-14   unknown, always 0xFF
    """

    COMMAND_LENGTH = 14

    DHW_ON = 0x0C
    DHW_OFF = 0x08
    HEATING_ON = 0x03
    HEATING_OFF = 0x02

    dhw_on: t.Optional[bool] = None
    dhw_target_temperature: t.Optional[float] = None
    heating_on: t.Optional[bool] = None
    zone1_target_temperature: t.Optional[float] = None
    zone2_target_temperature: t.Optional[float] = None
    function_register: t.Optional[int] = None

    def encode(self) -> str:
        data = [ToshibaEstiaFcuState.NONE_VAL] * self.COMMAND_LENGTH

        if self.dhw_on is not None:
            data[0] = self.DHW_ON if self.dhw_on else self.DHW_OFF

        if self.dhw_target_temperature is not None:
            data[1] = ToshibaEstiaFcuState.Temperature.to_raw(self.dhw_target_temperature)

        if self.heating_on is not None:
            data[2] = self.HEATING_ON if self.heating_on else self.HEATING_OFF

        if self.zone1_target_temperature is not None:
            data[4] = ToshibaEstiaFcuState.Temperature.to_raw(self.zone1_target_temperature)

        if self.zone2_target_temperature is not None:
            data[5] = ToshibaEstiaFcuState.Temperature.to_raw(self.zone2_target_temperature)

        if self.function_register is not None:
            if not 0 <= self.function_register < ToshibaEstiaFcuState.NONE_VAL:
                raise ValueError(f"function register 0x{self.function_register:x} cannot be encoded")

            data[9] = self.function_register

        return bytes(data).hex()
