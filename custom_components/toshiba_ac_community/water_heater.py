"""Platform for water heater integration.

Only Toshiba Estia heat pumps have a domestic hot water circuit; AC units add
no entity here.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.components.water_heater.const import STATE_HEAT_PUMP
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature

from .const import DOMAIN
from .entity import ToshibaEstiaStateEntity
from .toshiba_ac.device.estia import ToshibaEstiaDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add water heater entities for passed config_entry in HA."""
    device_manager = hass.data[DOMAIN][config_entry.entry_id]

    estia_devices = await device_manager.get_estia_devices()
    new_entities = [ToshibaEstiaDomesticHotWater(device) for device in estia_devices]

    if new_entities:
        _LOGGER.info("Adding %d water heater entities", len(new_entities))
        async_add_devices(new_entities)


class ToshibaEstiaDomesticHotWater(ToshibaEstiaStateEntity, WaterHeaterEntity):
    """The hot water tank of a Toshiba Estia heat pump.

    Sets the tank target and switches hot water on or off. The operation is
    that switch (heat pump or off), so the thermostat card can offer it like
    the zones' heat/off; what heats the tank right now is left to the
    compressor status sensor and the DHW electric heater binary sensor. The
    electric DHW boost is a switch entity of its own.
    """

    _attr_translation_key = "dhw"
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_operation_list = [STATE_HEAT_PUMP, STATE_OFF]

    def __init__(self, toshiba_device: ToshibaEstiaDevice):
        """Initialize the water heater entity."""
        super().__init__(toshiba_device)
        self._attr_unique_id = f"{self._device.ac_unique_id}_dhw"

    @property
    def current_operation(self) -> str:
        """Return whether hot water is switched on."""
        return STATE_HEAT_PUMP if self._device.dhw_is_enabled else STATE_OFF

    @property
    def current_temperature(self) -> float | None:
        """Return the water temperature leaving the tank.

        Not the temperature inside it - the heat pump reports no tank sensor.
        """
        return self._device.tho_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature the tank is set to reach."""
        return self._device.dhw_target_temperature

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self._device.dhw_min_temperature

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._device.dhw_max_temperature

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the tank target."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        await self._async_command(self._device.set_dhw_target_temperature(temperature))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Switch hot water on (heat pump) or off."""
        await self._async_command(
            self._device.set_dhw_on(operation_mode != STATE_OFF)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch hot water on."""
        await self._async_command(self._device.set_dhw_on(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch hot water off."""
        await self._async_command(self._device.set_dhw_on(False))
