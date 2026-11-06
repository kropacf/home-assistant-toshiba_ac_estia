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

from ..device.estia_command import ToshibaEstiaCommand
from ..device.estia_fcu_state import ToshibaEstiaFcuState
from ..device.properties import (
    EstiaCompressorStatus,
    EstiaFunctionFlag,
    EstiaSetpointLimits,
    EstiaWaterMode,
    EstiaWaterTemperatures,
    ToshibaAcDeviceEnergyConsumption,
)
from ..utils import async_sleep_until_next_multiply_of_minutes, ToshibaAcCallback
from ..utils.amqp_api import ToshibaAcAmqpApi, JSONSerializable
from ..utils.http_api import ToshibaAcHttpApi

logger = logging.getLogger(__name__)


class ToshibaEstiaDeviceError(Exception):
    pass


class ToshibaEstiaDeviceCallback(ToshibaAcCallback["ToshibaEstiaDevice"]):
    pass


class ToshibaEstiaDevice:
    """An Estia air-to-water heat pump on the Toshiba cloud.

    Commands go out the way the Toshiba app sends them: CMD_HDU_TO_ESTIA over
    the REST command endpoint, see ToshibaEstiaCommand for the payload. The unit
    answers each one with a status diff that arrives like any other state push.

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

    COMMAND = "CMD_HDU_TO_ESTIA"

    # Used until the device record has been read, or when it lacks the limits.
    # They match the ranges in the Estia installation manual and the record of
    # the unit the protocol was captured from.
    DEFAULT_DHW_MIN = 40.0
    DEFAULT_DHW_MAX = 75.0
    DEFAULT_ZONE_HEAT_MIN = 20.0
    DEFAULT_ZONE_HEAT_MAX = 65.0

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
        self.limits = EstiaSetpointLimits()

        self._water_flow_rate: t.Optional[int] = None
        self._is_online = False
        self._ac_energy_consumption: t.Optional[ToshibaAcDeviceEnergyConsumption] = None

        self._on_state_changed_callback = ToshibaEstiaDeviceCallback()
        self._on_energy_consumption_changed_callback = ToshibaEstiaDeviceCallback()

        self.periodic_reload_state_task: t.Optional[asyncio.Task[None]] = None
        self.load_additional_device_info_task: t.Optional[asyncio.Task[None]] = None

        # Serialises commands that rewrite the function register, so that two
        # quick toggles cannot both be built from the same old state
        self._command_lock = asyncio.Lock()

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
        self.limits = additional_info.limits

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

    async def send_command(self, command: ToshibaEstiaCommand) -> None:
        data = command.encode()
        logger.debug(f"[{self.name}] Sending command: {command} ({data})")
        await self.http_api.send_command(self.device_id, self.ac_unique_id, self.COMMAND, data)

    async def set_dhw_on(self, on: bool) -> None:
        await self.send_command(ToshibaEstiaCommand(dhw_on=on))

    async def set_dhw_target_temperature(self, temperature: float) -> None:
        self._check_range("hot water target", temperature, self.dhw_min_temperature, self.dhw_max_temperature)
        await self.send_command(ToshibaEstiaCommand(dhw_target_temperature=temperature))

    async def set_heating_on(self, on: bool) -> None:
        """Switch space heating on or off, for zone 1 and zone 2 together.

        The unit has one switch for both zones; there is no known way to switch
        a single zone.
        """
        await self.send_command(ToshibaEstiaCommand(heating_on=on))

    async def set_zone1_target_temperature(self, temperature: float) -> None:
        self._check_range(
            "zone 1 target", temperature, self.zone1_min_temperature, self.zone1_max_temperature
        )
        await self.send_command(ToshibaEstiaCommand(zone1_target_temperature=temperature))

    async def set_zone2_target_temperature(self, temperature: float) -> None:
        self._check_range(
            "zone 2 target", temperature, self.zone2_min_temperature, self.zone2_max_temperature
        )
        await self.send_command(ToshibaEstiaCommand(zone2_target_temperature=temperature))

    async def set_auto_mode(self, on: bool) -> None:
        """Switch the heating curve on or off, for both zones.

        Switching back to manual re-sends the zone 1 setpoint, as the Toshiba
        app does.
        """
        zone1_target = None if on else self.zone1_target_temperature
        await self._set_function_flag(EstiaFunctionFlag.AUTO, on, zone1_target_temperature=zone1_target)

    async def set_function(self, flag: EstiaFunctionFlag, on: bool) -> None:
        """Switch frost protection, night setback, DHW boost or anti-bacterial."""
        if flag == EstiaFunctionFlag.AUTO:
            await self.set_auto_mode(on)
            return

        await self._set_function_flag(flag, on)

    async def _set_function_flag(
        self,
        flag: EstiaFunctionFlag,
        on: bool,
        zone1_target_temperature: t.Optional[float] = None,
    ) -> None:
        async with self._command_lock:
            try:
                register = self.fcu_state.function_register()
            except ValueError as e:
                raise ToshibaEstiaDeviceError(f"[{self.name}] Refusing to rewrite the function register: {e}") from e

            register = register | int(flag) if on else register & ~int(flag)

            await self.send_command(
                ToshibaEstiaCommand(function_register=register, zone1_target_temperature=zone1_target_temperature)
            )

            # The unit acknowledges within a second, but a second toggle can come
            # sooner and would be built from the old register, undoing this one.
            # So apply what the unit will report right away: byte 19 carries the
            # functions, bytes 11 and 18 the per-zone auto flag. A rejected command
            # is corrected by the next full state reload.
            auto = "01" if register & int(EstiaFunctionFlag.AUTO) else "00"
            functions = f"{register & ToshibaEstiaFcuState.STATUS_FUNCTION_FLAGS:02x}"
            expected = "ff" * 10 + auto + "ff" * 6 + auto + functions

            if self.fcu_state.update(expected):
                await self.state_changed()

    def _check_range(self, what: str, value: float, low: float, high: float) -> None:
        if not low <= value <= high:
            raise ToshibaEstiaDeviceError(f"[{self.name}] {what} {value} is outside {low} - {high}")

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
    def dhw_is_enabled(self) -> bool:
        return self.fcu_state.dhw_is_enabled

    @property
    def zone1_is_on(self) -> bool:
        return self.fcu_state.zone1_is_on

    @property
    def has_zone2(self) -> bool:
        return self.fcu_state.has_zone2

    @property
    def zone2_is_on(self) -> bool:
        return self.fcu_state.zone2_is_on

    @property
    def zone2_mode(self) -> EstiaWaterMode:
        return self.fcu_state.zone2_mode

    @property
    def zone2_target_temperature(self) -> t.Optional[float]:
        return self.fcu_state.zone2_target_temperature

    @property
    def zone1_applied_temperature(self) -> t.Optional[float]:
        """Return the zone 1 water target in use, the heating-curve value in auto."""
        return self.fcu_state.zone1_applied_temperature

    @property
    def zone2_applied_temperature(self) -> t.Optional[float]:
        """Return the zone 2 water target in use, the heating-curve value in auto."""
        return self.fcu_state.zone2_applied_temperature

    @property
    def auto_mode(self) -> t.Optional[bool]:
        """Return whether the heating curve is on, None if not reported."""
        return self.fcu_state.zone1_auto

    def function_is_on(self, flag: EstiaFunctionFlag) -> bool:
        if flag == EstiaFunctionFlag.AUTO:
            return bool(self.auto_mode)

        return self.fcu_state.has_function_flag(flag)

    @property
    def dhw_min_temperature(self) -> float:
        return _limit(self.limits.dhw_min, self.DEFAULT_DHW_MIN)

    @property
    def dhw_max_temperature(self) -> float:
        return _limit(self.limits.dhw_max, self.DEFAULT_DHW_MAX)

    @property
    def zone1_min_temperature(self) -> float:
        return _limit(self.limits.zone1_heat_min, self.DEFAULT_ZONE_HEAT_MIN)

    @property
    def zone1_max_temperature(self) -> float:
        return _limit(self.limits.zone1_heat_max, self.DEFAULT_ZONE_HEAT_MAX)

    @property
    def zone2_min_temperature(self) -> float:
        return _limit(self.limits.zone2_heat_min, self.DEFAULT_ZONE_HEAT_MIN)

    @property
    def zone2_max_temperature(self) -> float:
        return _limit(self.limits.zone2_heat_max, self.DEFAULT_ZONE_HEAT_MAX)

    @property
    def zone1_outdoor_unit_is_active(self) -> bool:
        return self.fcu_state.zone1_outdoor_unit_is_active

    @property
    def zone2_outdoor_unit_is_active(self) -> bool:
        return self.fcu_state.zone2_outdoor_unit_is_active

    @property
    def zone2_electric_coil_is_active(self) -> bool:
        return self.fcu_state.zone2_electric_coil_is_active

    @property
    def compressor_status(self) -> EstiaCompressorStatus:
        return self.fcu_state.compressor_status

    @property
    def water_pump_status(self) -> bool:
        # Status byte 20; whether it really means "pump running" is doubtful,
        # see ToshibaEstiaFcuState.decode
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


def _limit(value: t.Optional[float], default: float) -> float:
    return default if value is None else value
