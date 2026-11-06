"""Binary sensor platform for the Toshiba AC integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)

from .const import DOMAIN
from .entity import ToshibaAcStateEntity, ToshibaEstiaStateEntity
from .toshiba_ac.device import ToshibaAcDevice, ToshibaAcSelfCleaning
from .toshiba_ac.device.estia import ToshibaEstiaDevice

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ToshibaEstiaBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a binary sensor read off an Estia heat pump."""

    value_fn: Callable[[ToshibaEstiaDevice], bool]


ESTIA_BINARY_SENSORS: tuple[ToshibaEstiaBinarySensorDescription, ...] = (
    ToshibaEstiaBinarySensorDescription(
        key="water_pump_status",
        translation_key="water_pump",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda device: device.water_pump_status,
    ),
    ToshibaEstiaBinarySensorDescription(
        key="electric_coil_heat_is_active",
        translation_key="heat_electric_heater",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda device: device.electric_coil_heat_is_active,
    ),
    ToshibaEstiaBinarySensorDescription(
        key="electric_coil_dhw_is_active",
        translation_key="dhw_electric_heater",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda device: device.electric_coil_dhw_is_active,
    ),
)


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add binary sensor entities for passed config_entry in HA."""
    device_manager = hass.data[DOMAIN][config_entry.entry_id]
    new_entities = []

    devices: list[ToshibaAcDevice] = await device_manager.get_devices()
    for device in devices:
        # Support is only detectable from the reported state: no report means
        # the unit has no self cleaning function
        if device.ac_self_cleaning is not ToshibaAcSelfCleaning.NONE:
            new_entities.append(ToshibaAcSelfCleaningEntity(device))
        else:
            _LOGGER.debug("AC device %s does not support self cleaning", device.name)

    estia_devices: list[ToshibaEstiaDevice] = await device_manager.get_estia_devices()
    for estia_device in estia_devices:
        new_entities.extend(
            ToshibaEstiaBinarySensor(estia_device, description)
            for description in ESTIA_BINARY_SENSORS
        )

    if new_entities:
        _LOGGER.info("Adding %d binary sensor entities", len(new_entities))
        async_add_devices(new_entities)


class ToshibaAcSelfCleaningEntity(ToshibaAcStateEntity, BinarySensorEntity):
    """Reports whether the AC is currently self cleaning."""

    _attr_has_entity_name = True
    _attr_translation_key = "self_cleaning"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:broom"

    def __init__(self, device: ToshibaAcDevice):
        """Initialize the binary sensor."""
        super().__init__(device)
        self._attr_unique_id = f"{device.ac_unique_id}_self_cleaning"

    @property
    def is_on(self) -> bool:
        """Return True if the AC is self cleaning."""
        return self._device.ac_self_cleaning == ToshibaAcSelfCleaning.ON


class ToshibaEstiaBinarySensor(ToshibaEstiaStateEntity, BinarySensorEntity):
    """Reports a single on/off state of an Estia heat pump."""

    entity_description: ToshibaEstiaBinarySensorDescription

    def __init__(
        self,
        device: ToshibaEstiaDevice,
        description: ToshibaEstiaBinarySensorDescription,
    ):
        """Initialize the binary sensor."""
        super().__init__(device)
        self.entity_description = description
        # Suffix kept for compatibility with the standalone toshiba_estia integration
        self._attr_unique_id = f"{device.ac_unique_id}_{description.key}_binary_sensor"

    @property
    def is_on(self) -> bool:
        """Return the value reported by the sensor."""
        return self.entity_description.value_fn(self._device)
