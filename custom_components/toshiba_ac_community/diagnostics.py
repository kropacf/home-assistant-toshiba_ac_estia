"""Diagnostics support for Toshiba AC."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {
    "username",
    "password",
    "sas_token",
    "access_token",
    "consumer_id",
    "device_id",
    "ac_id",
    "ac_unique_id",
    "serial",
    "mac",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device_manager = hass.data[DOMAIN].get(entry.entry_id)

    diagnostics_data: dict[str, Any] = {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
    }

    if device_manager is None:
        diagnostics_data["error"] = "Device manager not found"
        return diagnostics_data

    try:
        devices = await device_manager.get_devices()
        devices_data = []
        for device in devices:
            device_info = {
                "name": device.name,
                "ac_id": "**REDACTED**",
                "ac_unique_id": "**REDACTED**",
                "device_id": "**REDACTED**",
                "firmware_version": device.firmware_version,
                "merit_feature": device.merit_feature,
                "ac_model_id": device.ac_model_id,
                "ac_status": device.ac_status.name if device.ac_status else None,
                "ac_mode": device.ac_mode.name if device.ac_mode else None,
                "ac_temperature": device.ac_temperature,
                "ac_indoor_temperature": device.ac_indoor_temperature,
                "ac_outdoor_temperature": device.ac_outdoor_temperature,
                "ac_fan_mode": device.ac_fan_mode.name if device.ac_fan_mode else None,
                "ac_swing_mode": device.ac_swing_mode.name
                if device.ac_swing_mode
                else None,
                "ac_power_selection": device.ac_power_selection.name
                if device.ac_power_selection
                else None,
                "ac_merit_a": device.ac_merit_a.name if device.ac_merit_a else None,
                "ac_merit_b": device.ac_merit_b.name if device.ac_merit_b else None,
                "ac_air_pure_ion": device.ac_air_pure_ion.name
                if device.ac_air_pure_ion
                else None,
                "ac_self_cleaning": device.ac_self_cleaning.name
                if device.ac_self_cleaning
                else None,
                "supported_features": {
                    "ac_mode": [m.name for m in device.supported.ac_mode]
                    if device.supported.ac_mode
                    else [],
                    "ac_fan_mode": [m.name for m in device.supported.ac_fan_mode]
                    if device.supported.ac_fan_mode
                    else [],
                    "ac_swing_mode": [m.name for m in device.supported.ac_swing_mode]
                    if device.supported.ac_swing_mode
                    else [],
                    "ac_power_selection": [
                        m.name for m in device.supported.ac_power_selection
                    ]
                    if device.supported.ac_power_selection
                    else [],
                    "ac_merit_a": [m.name for m in device.supported.ac_merit_a]
                    if device.supported.ac_merit_a
                    else [],
                    "ac_merit_b": [m.name for m in device.supported.ac_merit_b]
                    if device.supported.ac_merit_b
                    else [],
                    "ac_air_pure_ion": [
                        m.name for m in device.supported.ac_air_pure_ion
                    ]
                    if device.supported.ac_air_pure_ion
                    else [],
                    "ac_energy_report": device.supported.ac_energy_report,
                },
            }
            devices_data.append(device_info)

        diagnostics_data["devices"] = devices_data
        diagnostics_data["device_count"] = len(devices)
    except Exception as ex:
        diagnostics_data["error"] = f"Failed to get devices: {ex}"

    try:
        estia_devices = await device_manager.get_estia_devices()
        estia_devices_data = []
        for device in estia_devices:
            estia_devices_data.append(
                {
                    "name": device.name,
                    "ac_id": "**REDACTED**",
                    "ac_unique_id": "**REDACTED**",
                    "device_id": "**REDACTED**",
                    "firmware_version": device.firmware_version,
                    "merit_feature": device.merit_feature,
                    "ac_model_id": device.ac_model_id,
                    "cdu": device.cdu,
                    "fcu": device.fcu,
                    "is_online": device.is_online,
                    "zone1_mode": device.zone1_mode.name,
                    "zone1_target_temperature": device.zone1_target_temperature,
                    "dhw_target_temperature": device.dhw_target_temperature,
                    "compressor_status": device.compressor_status.name,
                    "water_pump_status": device.water_pump_status,
                    "electric_coil_dhw_is_active": device.electric_coil_dhw_is_active,
                    "electric_coil_heat_is_active": device.electric_coil_heat_is_active,
                    "twi_temperature": device.twi_temperature,
                    "two_temperature": device.two_temperature,
                    "tho_temperature": device.tho_temperature,
                    "to_temperature": device.to_temperature,
                    "tfi_temperature": device.tfi_temperature,
                    "room_water_temperature": device.room_water_temperature,
                    "water_flow_rate": device.water_flow_rate,
                    # The raw status string is the only way to tell apart "byte not
                    # understood yet" from "value really is zero" in a bug report
                    "status_string": device.fcu_state.status_string,
                }
            )

        diagnostics_data["estia_devices"] = estia_devices_data
        diagnostics_data["estia_device_count"] = len(estia_devices)
    except Exception as ex:
        diagnostics_data["estia_error"] = f"Failed to get Estia devices: {ex}"

    return diagnostics_data
