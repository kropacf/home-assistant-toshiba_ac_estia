"""Tests for the Estia entities that write to the heat pump.

Runs the real ToshibaEstiaDevice from the vendored library; only its HTTP API is
replaced by a mock, so the assertions are on the exact command payloads the
entities send. Payloads are the ones captured from the Toshiba app.

Run from the repo root:
    .venv/bin/python tests/test_estia_control.py
"""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.toshiba_ac_community import climate, switch, water_heater
from custom_components.toshiba_ac_community.climate import (
    ToshibaEstiaHeatingZone,
    ToshibaEstiaHeatingZone2,
)
from custom_components.toshiba_ac_community.const import DOMAIN
from custom_components.toshiba_ac_community.switch import ToshibaEstiaSwitchEntity
from custom_components.toshiba_ac_community.toshiba_ac.device.estia import (
    ToshibaEstiaDevice,
)
from custom_components.toshiba_ac_community.toshiba_ac.utils.http_api import (
    ToshibaAcHttpApiError,
)
from custom_components.toshiba_ac_community.water_heater import (
    ToshibaEstiaDomesticHotWater,
)
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.components.water_heater.const import STATE_HEAT_PUMP
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF
from homeassistant.exceptions import HomeAssistantError

# DHW on at 45 C, heating on, zone 1 at 30 C, zone 2 at 27 C, anti-bacterial on
STATE = "0c7a000003065c5c010000030656560100002006686868683462ff0000007a5c56484e52"
NO_ZONE_2 = STATE[:22] + "00" + STATE[24:]


def _device(state: str = STATE) -> ToshibaEstiaDevice:
    http_api = MagicMock()
    http_api.send_command = AsyncMock()
    return ToshibaEstiaDevice(
        "AW-1",
        "user_0123456789abcdef",
        "ac-id",
        "unit-1",
        state,
        "1.2.3",
        "0000000",
        "1",
        MagicMock(),
        http_api,
    )


def _sent(device: ToshibaEstiaDevice) -> list[str]:
    return [call.args[3] for call in device.http_api.send_command.await_args_list]


async def _setup(platform, device: ToshibaEstiaDevice) -> list:
    manager = MagicMock()
    manager.get_devices = AsyncMock(return_value=[])
    manager.get_estia_devices = AsyncMock(return_value=[device])
    hass = SimpleNamespace(data={DOMAIN: {"entry": manager}})
    added = []
    await platform.async_setup_entry(
        hass, SimpleNamespace(entry_id="entry"), added.extend
    )
    return added


async def _zone_2_only_when_reported():
    entities = await _setup(climate, _device())
    assert [type(e) for e in entities] == [
        ToshibaEstiaHeatingZone,
        ToshibaEstiaHeatingZone2,
    ]
    assert entities[1].unique_id == "unit-1_climate_zone2"

    entities = await _setup(climate, _device(NO_ZONE_2))
    assert [type(e) for e in entities] == [ToshibaEstiaHeatingZone]


async def _zone_1_control():
    device = _device()
    zone = ToshibaEstiaHeatingZone(device)

    assert zone.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.hvac_modes == [HVACMode.HEAT, HVACMode.OFF]
    assert zone.target_temperature == 30.0
    assert (zone.min_temp, zone.max_temp) == (20.0, 65.0)

    await zone.async_set_temperature(**{ATTR_TEMPERATURE: 31})
    await zone.async_set_hvac_mode(HVACMode.OFF)
    await zone.async_turn_on()

    assert _sent(device) == [
        "ffffffff5effffffffffffffffff",
        "ffff02ffffffffffffffffffffff",
        "ffff03ffffffffffffffffffffff",
    ]


async def _zone_2_control_and_shared_on_off():
    device = _device()
    zone = ToshibaEstiaHeatingZone2(device)

    assert zone.target_temperature == 27.0
    assert zone.hvac_mode == HVACMode.HEAT

    await zone.async_set_temperature(**{ATTR_TEMPERATURE: 28})
    # Zone 2 has no switch of its own: this is the heating switch of both zones
    await zone.async_turn_off()

    assert _sent(device) == [
        "ffffffffff58ffffffffffffffff",
        "ffff02ffffffffffffffffffffff",
    ]

    # The unit's ack switches both zones off
    await device.handle_cmd_hdu_from_estia(
        {"data": "ff" * 4 + "02" + "ff" * 6 + "02" + "ff" * 24}
    )
    assert zone.hvac_mode == HVACMode.OFF
    assert ToshibaEstiaHeatingZone(device).hvac_mode == HVACMode.OFF


async def _hot_water_control():
    device = _device()
    dhw = ToshibaEstiaDomesticHotWater(device)

    assert dhw.supported_features & WaterHeaterEntityFeature.TARGET_TEMPERATURE
    assert dhw.supported_features & WaterHeaterEntityFeature.ON_OFF
    assert dhw.supported_features & WaterHeaterEntityFeature.OPERATION_MODE
    assert dhw.operation_list == [STATE_HEAT_PUMP, STATE_OFF]
    assert dhw.current_operation == STATE_HEAT_PUMP
    assert (dhw.min_temp, dhw.max_temp) == (40.0, 75.0)

    await dhw.async_set_temperature(**{ATTR_TEMPERATURE: 46})
    await dhw.async_turn_off()
    await dhw.async_turn_on()
    await dhw.async_set_operation_mode(STATE_OFF)
    await dhw.async_set_operation_mode(STATE_HEAT_PUMP)

    assert _sent(device) == [
        "ff7cffffffffffffffffffffffff",
        "08ffffffffffffffffffffffffff",
        "0cffffffffffffffffffffffffff",
        "08ffffffffffffffffffffffffff",
        "0cffffffffffffffffffffffffff",
    ]

    await device.handle_cmd_hdu_from_estia({"data": "08" + "ff" * 35})
    assert dhw.current_operation == STATE_OFF


async def _function_switches():
    device = _device()
    entities = await _setup(switch, device)
    switches = {e.entity_description.key: e for e in entities}

    assert set(switches) == {
        "auto_mode",
        "night_setback",
        "dhw_boost",
        "frost_protection",
        "anti_bacterial",
    }
    assert all(isinstance(e, ToshibaEstiaSwitchEntity) for e in entities)
    assert switches["anti_bacterial"].is_on
    assert not switches["dhw_boost"].is_on
    assert not switches["auto_mode"].is_on

    await switches["dhw_boost"].async_turn_on()
    await switches["auto_mode"].async_turn_on()

    # Boost on keeps anti-bacterial; auto on keeps both
    assert _sent(device) == [
        "ff" * 9 + "30" + "ff" * 4,
        "ff" * 9 + "31" + "ff" * 4,
    ]
    assert switches["dhw_boost"].is_on
    assert switches["auto_mode"].is_on


async def _errors_reach_the_ui():
    # Unknown bits in the function register: refuse, send nothing
    device = _device(STATE[:36] + "60" + STATE[38:])
    night = ToshibaEstiaSwitchEntity(device, switch.ESTIA_SWITCHES[1])
    try:
        await night.async_turn_on()
    except HomeAssistantError:
        pass
    else:
        raise AssertionError("expected HomeAssistantError")
    assert _sent(device) == []

    # A cloud rejection
    device = _device()
    device.http_api.send_command = AsyncMock(
        side_effect=ToshibaAcHttpApiError("not authorized")
    )
    try:
        await ToshibaEstiaDomesticHotWater(device).async_turn_off()
    except HomeAssistantError:
        pass
    else:
        raise AssertionError("expected HomeAssistantError")


async def _water_heater_setup():
    entities = await _setup(water_heater, _device())
    assert len(entities) == 1


if __name__ == "__main__":
    asyncio.run(_zone_2_only_when_reported())
    asyncio.run(_zone_1_control())
    asyncio.run(_zone_2_control_and_shared_on_off())
    asyncio.run(_hot_water_control())
    asyncio.run(_function_switches())
    asyncio.run(_errors_reach_the_ui())
    asyncio.run(_water_heater_setup())
    print("ok")
