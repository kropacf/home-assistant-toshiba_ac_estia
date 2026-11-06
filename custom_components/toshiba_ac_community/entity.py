"""Base classes for Toshiba AC entities."""
from __future__ import annotations

import logging

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .toshiba_ac.device import ToshibaAcDevice
from .toshiba_ac.device.estia import ToshibaEstiaDevice

_LOGGER = logging.getLogger(__name__)


class ToshibaAcEntity(Entity):
    """Representation of a Toshiba AC device entity."""

    _attr_should_poll = False

    def __init__(self, toshiba_device: ToshibaAcDevice) -> None:
        """Initialize the entity."""
        self._device = toshiba_device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device.ac_unique_id)},
            model=f"Model {self._device.ac_model_id}"
            if self._device.ac_model_id
            else None,
            manufacturer="Toshiba",
            name=self._device.name,
            sw_version=self._device.firmware_version,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return bool(
            self._device.ac_id
            and self._device.amqp_api.sas_token
            and self._device.http_api.access_token
        )


class ToshibaAcStateEntity(ToshibaAcEntity):
    """Base class for entities that subscribe to the device's state_changed callback."""

    async def async_added_to_hass(self) -> None:
        """Subscribe to the device's state_changed callback."""
        self._device.on_state_changed_callback.add(self._state_changed)

    async def async_will_remove_from_hass(self) -> None:
        """Call when device is removed from HA."""
        self._device.on_state_changed_callback.remove(self._state_changed)

    def update_attrs(self) -> None:
        """Call when the Toshiba AC device state changes."""

    def _state_changed(self, _device: ToshibaAcDevice) -> None:
        """Call when the Toshiba AC device state changes."""
        self.update_attrs()
        self.async_write_ha_state()


class ToshibaEstiaEntity(Entity):
    """Representation of an entity of a Toshiba Estia heat pump."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, toshiba_device: ToshibaEstiaDevice) -> None:
        """Initialize the entity."""
        self._device = toshiba_device

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info.

        Built on every read rather than cached in __init__: the model names and
        the serial number arrive from a second request that is still in flight
        while the entities are created.
        """
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.ac_unique_id)},
            model=self._device.fcu,
            manufacturer="Toshiba",
            name=self._device.name,
            sw_version=self._device.firmware_version,
            serial_number=self._device.serial_number,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        A heat pump reports no on/off status of its own, so cloud connectivity is
        the only signal that separates "idle" from "not reporting".
        """
        return bool(
            self._device.ac_id
            and self._device.http_api.access_token
            and self._device.is_online
        )


class ToshibaEstiaStateEntity(ToshibaEstiaEntity):
    """Base class for Estia entities that subscribe to the state_changed callback."""

    async def async_added_to_hass(self) -> None:
        """Subscribe to the device's state_changed callback."""
        self._device.on_state_changed_callback.add(self._state_changed)

    async def async_will_remove_from_hass(self) -> None:
        """Call when device is removed from HA."""
        self._device.on_state_changed_callback.remove(self._state_changed)

    def update_attrs(self) -> None:
        """Call when the Toshiba Estia device state changes."""

    def _state_changed(self, _device: ToshibaEstiaDevice) -> None:
        """Call when the Toshiba Estia device state changes."""
        self.update_attrs()
        self.async_write_ha_state()
