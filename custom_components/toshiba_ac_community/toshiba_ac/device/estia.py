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

import asyncio
import logging
import typing as t

from ..device.estia_fcu_state import ToshibaEstiaFcuState
from ..device.properties import (
    EstiaCompressorStatus,
    EstiaWaterMode,
    EstiaWaterTemperatures,
    ToshibaAcDeviceEnergyConsumption,
)
from ..utils import async_sleep_until_next_multiply_of_minutes, ToshibaAcCallback
from ..utils.amqp_api import ToshibaAcAmqpApi, JSONSerializable
from ..utils.http_api import ToshibaAcHttpApi

logger = logging.getLogger(__name__)


class ToshibaEstiaDeviceCallback(ToshibaAcCallback["ToshibaEstiaDevice"]):
    pass


class ToshibaEstiaDevice:
    """An Estia air-to-water heat pump on the Toshiba cloud.

    Read-only: Toshiba's mobile protocol for writing to an Estia unit has not been
    reverse engineered, so this class exposes state and never sends commands.

    State arrives from three places, and the first two overlap almost entirely:
      - the status string, over AMQP (CMD_HDU_FROM_ESTIA) and from the state
        endpoint, carrying setpoints, compressor, pump, coils and the water sensors
      - the heartbeat, over AMQP (CMD_HEARTBEAT_ESTIA), carrying the water sensors
        and the flow rate
      - the extended info endpoint, for the model names and the serial number

    Because the status string alone covers everything a user sees, polling it keeps
    a heat pump reporting even while the push channel is down - which happens
    whenever another client of the same Toshiba account takes over the single cloud
    connection the account is allowed.
    """

    STATE_RELOAD_PERIOD_MINUTES = 30

    def __init__(
        self,
        name: str,
        device_id: str,
        ac_id: str,
        ac_unique_id: str,
        initial_ac_state: str,
        firmware_version: str,
        merit_feature: str,
        ac_model_id: str,
        amqp_api: ToshibaAcAmqpApi,
        http_api: ToshibaAcHttpApi,
    ) -> None:
        self.name = name
        self.device_id = device_id
        self.ac_id = ac_id
        self.ac_unique_id = ac_unique_id
        self.firmware_version = firmware_version
        self.merit_feature = merit_feature
        self.ac_model_id = ac_model_id
        self.amqp_api = amqp_api
        self.http_api = http_api

        self.fcu_state = ToshibaEstiaFcuState.from_hex_state(initial_ac_state)
        # The device mapping already carried a full status string, so the sensors
        # have values before the first poll or push arrives
        self.temperatures = self.fcu_state.water_temperatures

        self.cdu: t.Optional[str] = None
        self.fcu: t.Optional[str] = None
        self.serial_number: t.Optional[str] = None

        self._water_flow_rate: t.Optional[int] = None
        self._is_online = False
        self._ac_energy_consumption: t.Optional[ToshibaAcDeviceEnergyConsumption] = None

        self._on_state_changed_callback = ToshibaEstiaDeviceCallback()
        self._on_energy_consumption_changed_callback = ToshibaEstiaDeviceCallback()

        self.periodic_reload_state_task: t.Optional[asyncio.Task[None]] = None
        self.load_additional_device_info_task: t.Optional[asyncio.Task[None]] = None

    async def connect(self) -> None:
        self.load_additional_device_info_task = asyncio.create_task(self._load_additional_device_info_deferred())
        self.periodic_reload_state_task = asyncio.create_task(self.periodic_state_reload())

    async def shutdown(self) -> None:
        if self.load_additional_device_info_task:
            self.load_additional_device_info_task.cancel()
            await asyncio.gather(self.load_additional_device_info_task, return_exceptions=True)

        if self.periodic_reload_state_task:
            self.periodic_reload_state_task.cancel()
            await asyncio.gather(self.periodic_reload_state_task, return_exceptions=True)

    async def _load_additional_device_info_deferred(self) -> None:
        try:
            await self.load_additional_device_info()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[{self.name}] Failed to load additional device info: {e}")

    async def load_additional_device_info(self) -> None:
        """Read the model identification, and the water sensors along with it."""
        additional_info = await self.http_api.get_estia_device_additional_info(self.ac_unique_id)

        self.cdu = additional_info.cdu
        self.fcu = additional_info.fcu
        self.serial_number = additional_info.serial_number
        self.temperatures = additional_info.temperatures

        await self.on_state_changed_callback(self)

    async def state_reload(self) -> None:
        hex_state = await self.http_api.get_estia_device_state(self.ac_unique_id)
        logger.debug(f"[{self.name}] Estia state from HTTP: {hex_state}")

        if self.fcu_state.update(hex_state):
            self.temperatures = self.fcu_state.water_temperatures
            await self.state_changed()

    async def state_changed(self) -> None:
        logger.info(f"[{self.name}] Current state: {self.fcu_state}")
        await self.on_state_changed_callback(self)

    async def periodic_state_reload(self) -> None:
        while True:
            await async_sleep_until_next_multiply_of_minutes(self.STATE_RELOAD_PERIOD_MINUTES)
            try:
                await self.state_reload()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Best-effort periodic poll; AMQP push still delivers state, so not an error
                logger.warning(f"[{self.name}] State reload failed: {e}")

    async def handle_cmd_hdu_from_estia(self, payload: dict[str, JSONSerializable]) -> None:
        data = payload.get("data")

        if not isinstance(data, str):
            logger.error(f"[{self.name}] malformed Estia state from AMQP: {data}")
            return

        logger.debug(f"[{self.name}] Estia state from AMQP: {data}")

        if self.fcu_state.update(data):
            self.temperatures = self.fcu_state.water_temperatures
            await self.state_changed()

    async def handle_cmd_heartbeat_estia(self, payload: dict[str, t.Any]) -> None:
        logger.debug(f"[{self.name}] Estia heartbeat from AMQP: {payload}")

        temperatures = EstiaWaterTemperatures(
            twi=_parse_hex(payload.get("TWI_temp")),
            two=_parse_hex(payload.get("TWO_temp")),
            tho=_parse_hex(payload.get("THO_temp")),
            to=_parse_hex(payload.get("TO_temp")),
            tfi=_parse_hex(payload.get("TFI_temp")),
            room_water=_parse_hex(payload.get("roomWater_temp")),
        )
        water_flow_rate = _parse_hex(payload.get("FLO"))

        if temperatures == self.temperatures and water_flow_rate == self._water_flow_rate:
            return

        self.temperatures = temperatures
        self._water_flow_rate = water_flow_rate

        await self.state_changed()

    async def handle_connection_state(self, state: bool) -> None:
        if self._is_online == state:
            return

        logger.info(f"[{self.name}] Is online: {state}")

        self._is_online = state

        await self.state_changed()

    async def handle_update_ac_energy_consumption(self, val: ToshibaAcDeviceEnergyConsumption) -> None:
        if self._ac_energy_consumption != val:
            self._ac_energy_consumption = val

            logger.debug(f"[{self.name}] New energy consumption: {val.energy_wh}Wh")

            await self.on_energy_consumption_changed_callback(self)

    @property
    def zone1_mode(self) -> EstiaWaterMode:
        return self.fcu_state.zone1_mode

    @property
    def zone1_target_temperature(self) -> t.Optional[float]:
        return self.fcu_state.zone1_target_temperature

    @property
    def dhw_target_temperature(self) -> t.Optional[float]:
        return self.fcu_state.dhw_target_temperature

    @property
    def compressor_status(self) -> EstiaCompressorStatus:
        return self.fcu_state.compressor_status

    @property
    def water_pump_status(self) -> bool:
        return self.fcu_state.water_pump_is_running

    @property
    def electric_coil_dhw_is_active(self) -> bool:
        return self.fcu_state.electric_coil_dhw_is_active

    @property
    def electric_coil_heat_is_active(self) -> bool:
        return self.fcu_state.electric_coil_heat_is_active

    @property
    def twi_temperature(self) -> t.Optional[float]:
        """Water temperature entering the heat exchanger."""
        return ToshibaEstiaFcuState.WaterTemperature.from_raw(self.temperatures.twi)

    @property
    def two_temperature(self) -> t.Optional[float]:
        """Water temperature leaving the heat exchanger."""
        return ToshibaEstiaFcuState.WaterTemperature.from_raw(self.temperatures.two)

    @property
    def tho_temperature(self) -> t.Optional[float]:
        """Water temperature leaving the water heater."""
        return ToshibaEstiaFcuState.WaterTemperature.from_raw(self.temperatures.tho)

    @property
    def to_temperature(self) -> t.Optional[float]:
        """Outdoor temperature measured by the heat pump."""
        return ToshibaEstiaFcuState.WaterTemperature.from_raw(self.temperatures.to)

    @property
    def tfi_temperature(self) -> t.Optional[float]:
        """Water temperature entering the floor heating circuit."""
        return ToshibaEstiaFcuState.WaterTemperature.from_raw(self.temperatures.tfi)

    @property
    def room_water_temperature(self) -> t.Optional[float]:
        return ToshibaEstiaFcuState.WaterTemperature.from_raw(self.temperatures.room_water)

    @property
    def water_flow_rate(self) -> t.Optional[float]:
        if self._water_flow_rate is None:
            return None

        return self._water_flow_rate / 10

    @property
    def ac_energy_consumption(self) -> t.Optional[ToshibaAcDeviceEnergyConsumption]:
        return self._ac_energy_consumption

    @property
    def is_online(self) -> bool:
        return self._is_online

    @property
    def on_state_changed_callback(self) -> ToshibaEstiaDeviceCallback:
        return self._on_state_changed_callback

    @property
    def on_energy_consumption_changed_callback(self) -> ToshibaEstiaDeviceCallback:
        return self._on_energy_consumption_changed_callback


def _parse_hex(value: t.Any) -> t.Optional[int]:
    if not isinstance(value, str):
        return None

    try:
        return int(value, 16)
    except ValueError:
        logger.warning(f"Ignoring malformed hex value in Estia heartbeat: {value!r}")
        return None
