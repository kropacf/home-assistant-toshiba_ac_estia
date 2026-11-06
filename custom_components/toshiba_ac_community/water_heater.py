"""Platform for water heater integration.

Only Toshiba Estia heat pumps have a domestic hot water circuit; AC units add
no entity here.
"""
from __future__ import annotations

import logging

from .toshiba_ac.device.estia import ToshibaEstiaDevice
from .toshiba_ac.device.properties import EstiaCompressorStatus

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.components.water_heater.const import STATE_ELECTRIC, STATE_HEAT_PUMP
from homeassistant.const import STATE_OFF, UnitOfTemperature

from .const import DOMAIN
from .entity import ToshibaEstiaStateEntity

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

    Read-only: Toshiba's protocol for writing to an Estia unit has not been
    reverse engineered, so no feature flag is advertised and the entity exposes
    no setters. Use the Toshiba app or the wall controller to change anything.
    """

    _attr_translation_key = "dhw"
    _attr_supported_features = WaterHeaterEntityFeature(0)
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_operation_list = [STATE_OFF, STATE_HEAT_PUMP, STATE_ELECTRIC]

    def __init__(self, toshiba_device: ToshibaEstiaDevice):
        """Initialize the water heater entity."""
        super().__init__(toshiba_device)
        self._attr_unique_id = f"{self._device.ac_unique_id}_dhw"

    @property
    def current_operation(self) -> str:
        """Return what is currently heating the tank.

        The immersion heater only ever runs as a supplement to the compressor,
        so reporting it takes precedence: that is the expensive case a user
        wants to see.
        """
        if self._device.electric_coil_dhw_is_active:
            return STATE_ELECTRIC

        if self._device.compressor_status == EstiaCompressorStatus.DHW:
            return STATE_HEAT_PUMP

        return STATE_OFF

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
        return 20

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return 60
