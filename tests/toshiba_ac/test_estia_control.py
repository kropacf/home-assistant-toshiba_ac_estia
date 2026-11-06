"""Tests for writing to an Estia heat pump.

Every expected payload below is one the Toshiba app was captured sending to a real
unit, which applied it (see estia-re/FINDINGS.md). Nothing here talks to the
network: the HTTP session is replaced by a fake that records the request.
"""

import itertools
import re
import unittest
from unittest.mock import AsyncMock, MagicMock

from custom_components.toshiba_ac_community.toshiba_ac.device.estia import (
    ToshibaEstiaDevice,
    ToshibaEstiaDeviceError,
)
from custom_components.toshiba_ac_community.toshiba_ac.device.estia_command import (
    ToshibaEstiaCommand,
)
from custom_components.toshiba_ac_community.toshiba_ac.device.estia_fcu_state import (
    ToshibaEstiaFcuState,
)
from custom_components.toshiba_ac_community.toshiba_ac.device.properties import (
    EstiaFunctionFlag,
    EstiaWaterMode,
)
from custom_components.toshiba_ac_community.toshiba_ac.utils.http_api import (
    ToshibaAcHttpApi,
    ToshibaAcHttpApiError,
    ToshibaAcHttpApiRateLimitError,
)

# Full status captured after the tests were reverted: DHW on at 45 C, heating on,
# zone 1 at 30 C and zone 2 at 27 C, both manual, anti-bacterial on
STATE = "0c7a000003065c5c010000030656560100002006686868683462ff0000007a5c56484e52"
# The same unit with the heating curve on (status bytes 11 and 18 = 01)
STATE_AUTO = STATE[:20] + "01" + STATE[22:34] + "01" + STATE[36:]

SOURCE_ID = "user@example.com_0123456789abcdef"


def make_device(state: str = STATE) -> ToshibaEstiaDevice:
    http_api = MagicMock()
    http_api.send_command = AsyncMock()

    return ToshibaEstiaDevice(
        "AW-1",
        SOURCE_ID,
        "ac-id",
        "unique-id",
        state,
        "1.2.3",
        "0000000",
        "1",
        MagicMock(),
        http_api,
    )


def sent_payloads(device: ToshibaEstiaDevice) -> list[str]:
    calls = device.http_api.send_command.await_args_list
    for call in calls:
        source_id, target_id, cmd, _ = call.args
        assert (source_id, target_id, cmd) == (SOURCE_ID, "unique-id", "CMD_HDU_TO_ESTIA")
    return [call.args[3] for call in calls]


class EstiaCommandEncodingTest(unittest.TestCase):
    def test_empty_command_leaves_everything_unchanged(self) -> None:
        self.assertEqual(ToshibaEstiaCommand().encode(), "ff" * 14)

    def test_captured_payloads(self) -> None:
        cases = {
            "ff7cffffffffffffffffffffffff": ToshibaEstiaCommand(dhw_target_temperature=46),
            "ff7affffffffffffffffffffffff": ToshibaEstiaCommand(dhw_target_temperature=45),
            "ffffffff5effffffffffffffffff": ToshibaEstiaCommand(zone1_target_temperature=31),
            "ffffffff5cffffffffffffffffff": ToshibaEstiaCommand(zone1_target_temperature=30),
            "ffffffffff58ffffffffffffffff": ToshibaEstiaCommand(zone2_target_temperature=28),
            "ffffffffff56ffffffffffffffff": ToshibaEstiaCommand(zone2_target_temperature=27),
            "08ffffffffffffffffffffffffff": ToshibaEstiaCommand(dhw_on=False),
            "0cffffffffffffffffffffffffff": ToshibaEstiaCommand(dhw_on=True),
            "ffff02ffffffffffffffffffffff": ToshibaEstiaCommand(heating_on=False),
            "ffff03ffffffffffffffffffffff": ToshibaEstiaCommand(heating_on=True),
            "ffffffffffffffffff21ffffffff": ToshibaEstiaCommand(function_register=0x21),
            "ffffffff5cffffffff20ffffffff": ToshibaEstiaCommand(zone1_target_temperature=30, function_register=0x20),
        }

        for expected, command in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(command.encode(), expected)

    def test_half_degrees_are_encoded(self) -> None:
        self.assertEqual(ToshibaEstiaCommand(dhw_target_temperature=45.5).encode()[2:4], "7b")

    def test_a_temperature_that_would_read_as_unchanged_is_rejected(self) -> None:
        # 111.5 C encodes as 0xFF, the "leave unchanged" marker
        with self.assertRaises(ValueError):
            ToshibaEstiaCommand(dhw_target_temperature=111.5).encode()

        with self.assertRaises(ValueError):
            ToshibaEstiaCommand(zone1_target_temperature=-20).encode()

    def test_an_out_of_range_register_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ToshibaEstiaCommand(function_register=0xFF).encode()


class EstiaStatusDecodingTest(unittest.TestCase):
    def test_decodes_both_zones(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE)

        self.assertTrue(state.zone1_is_on)
        self.assertEqual(state.zone1_target_temperature, 30.0)
        self.assertEqual(state.zone1_mode, EstiaWaterMode.HEAT)
        self.assertTrue(state.has_zone2)
        self.assertTrue(state.zone2_is_on)
        self.assertEqual(state.zone2_target_temperature, 27.0)
        self.assertEqual(state.zone2_mode, EstiaWaterMode.HEAT)
        self.assertTrue(state.zone2_outdoor_unit_is_active)
        self.assertFalse(state.zone2_electric_coil_is_active)

    def test_decodes_auto_flags_from_bytes_11_and_18(self) -> None:
        self.assertFalse(ToshibaEstiaFcuState.from_hex_state(STATE).zone1_auto)

        state = ToshibaEstiaFcuState.from_hex_state(STATE_AUTO)
        self.assertTrue(state.zone1_auto)
        self.assertTrue(state.zone2_auto)

    def test_decodes_function_flags_from_byte_19(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE)

        self.assertEqual(state.function_flags, 0x20)
        self.assertTrue(state.has_function_flag(EstiaFunctionFlag.ANTI_BACTERIAL))
        self.assertFalse(state.has_function_flag(EstiaFunctionFlag.NIGHT_SETBACK))
        self.assertFalse(state.has_function_flag(EstiaFunctionFlag.DHW_BOOST))
        self.assertFalse(state.has_function_flag(EstiaFunctionFlag.FROST_PROTECTION))

    def test_decodes_dhw_and_heating_on_off(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE)
        self.assertTrue(state.dhw_is_enabled)

        # The acks of the DHW-off and heating-off commands
        state.update("08" + "ff" * 35)
        self.assertFalse(state.dhw_is_enabled)

        state.update("ff" * 4 + "02" + "ff" * 6 + "02" + "ff" * 24)
        self.assertFalse(state.zone1_is_on)
        self.assertFalse(state.zone2_is_on)
        self.assertTrue(state.has_zone2)

    def test_a_unit_without_zone_2(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE[:22] + "00" + STATE[24:])

        self.assertFalse(state.has_zone2)
        self.assertFalse(state.zone2_is_on)

    def test_compressor_heating_counts_either_zone(self) -> None:
        # Zone 1 compressor flag off, zone 2 still on
        state = ToshibaEstiaFcuState.from_hex_state(STATE[:16] + "00" + STATE[18:])

        self.assertFalse(state.zone1_outdoor_unit_is_active)
        self.assertEqual(state.compressor_status.name, "HEAT")


class EstiaFunctionRegisterTest(unittest.TestCase):
    def test_register_is_byte_19_plus_the_auto_flag(self) -> None:
        self.assertEqual(ToshibaEstiaFcuState.from_hex_state(STATE).function_register(), 0x20)
        self.assertEqual(ToshibaEstiaFcuState.from_hex_state(STATE_AUTO).function_register(), 0x21)

    def test_all_known_flags_are_preserved(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE_AUTO[:36] + "3a" + STATE_AUTO[38:])

        self.assertEqual(state.function_register(), 0x3B)

    def test_unknown_bits_are_refused_rather_than_sent_or_dropped(self) -> None:
        for byte_19 in ("24", "60", "a0", "21"):
            with self.subTest(byte_19=byte_19):
                state = ToshibaEstiaFcuState.from_hex_state(STATE[:36] + byte_19 + STATE[38:])

                with self.assertRaises(ValueError):
                    state.function_register()

    def test_an_unknown_auto_flag_is_refused(self) -> None:
        state = ToshibaEstiaFcuState.from_hex_state(STATE[:20] + "05" + STATE[22:])

        with self.assertRaises(ValueError):
            state.function_register()


class EstiaDeviceCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_setters_send_the_captured_payloads(self) -> None:
        device = make_device()

        await device.set_dhw_target_temperature(46)
        await device.set_zone1_target_temperature(31)
        await device.set_zone2_target_temperature(28)
        await device.set_dhw_on(False)
        await device.set_heating_on(False)

        self.assertEqual(
            sent_payloads(device),
            [
                "ff7cffffffffffffffffffffffff",
                "ffffffff5effffffffffffffffff",
                "ffffffffff58ffffffffffffffff",
                "08ffffffffffffffffffffffffff",
                "ffff02ffffffffffffffffffffff",
            ],
        )

    async def test_function_switches_send_the_captured_registers(self) -> None:
        # Each starts from the captured state, anti-bacterial on
        cases = [
            (EstiaFunctionFlag.NIGHT_SETBACK, True, "28"),
            (EstiaFunctionFlag.DHW_BOOST, True, "30"),
            (EstiaFunctionFlag.FROST_PROTECTION, True, "22"),
            (EstiaFunctionFlag.ANTI_BACTERIAL, False, "00"),
            (EstiaFunctionFlag.AUTO, True, "21"),
        ]

        for flag, on, register in cases:
            with self.subTest(flag=flag, on=on):
                device = make_device()

                await device.set_function(flag, on)

                self.assertEqual(sent_payloads(device), ["ff" * 9 + register + "ff" * 4])

    async def test_switching_to_manual_resends_the_zone_1_setpoint(self) -> None:
        device = make_device(STATE_AUTO)

        await device.set_auto_mode(False)

        # Exactly what the app sent: zone 1 at 30 C, register 0x20
        self.assertEqual(sent_payloads(device), ["ffffffff5cffffffff20ffffffff"])

    async def test_switching_one_function_keeps_the_others(self) -> None:
        device = make_device(STATE_AUTO[:36] + "3a" + STATE_AUTO[38:])

        await device.set_function(EstiaFunctionFlag.NIGHT_SETBACK, False)

        self.assertEqual(sent_payloads(device), ["ff" * 9 + "33" + "ff" * 4])

    async def test_quick_toggles_do_not_undo_each_other(self) -> None:
        # The second command must include the first one even though the unit
        # has not acknowledged it yet
        device = make_device()
        callback = AsyncMock()
        device.on_state_changed_callback.add(callback)

        await device.set_function(EstiaFunctionFlag.NIGHT_SETBACK, True)
        await device.set_function(EstiaFunctionFlag.DHW_BOOST, True)

        self.assertEqual(sent_payloads(device), ["ff" * 9 + "28" + "ff" * 4, "ff" * 9 + "38" + "ff" * 4])
        self.assertTrue(device.function_is_on(EstiaFunctionFlag.NIGHT_SETBACK))
        self.assertTrue(device.function_is_on(EstiaFunctionFlag.DHW_BOOST))
        self.assertEqual(callback.await_count, 2)

    async def test_auto_is_applied_to_both_zone_flags(self) -> None:
        device = make_device()

        await device.set_auto_mode(True)

        self.assertTrue(device.auto_mode)
        self.assertTrue(device.fcu_state.zone2_auto)
        self.assertEqual(device.fcu_state.function_flags, 0x20)

    async def test_unknown_function_bits_block_the_switches_and_send_nothing(self) -> None:
        device = make_device(STATE[:36] + "60" + STATE[38:])

        with self.assertRaises(ToshibaEstiaDeviceError):
            await device.set_function(EstiaFunctionFlag.NIGHT_SETBACK, True)

        device.http_api.send_command.assert_not_awaited()

    async def test_the_unit_ack_is_merged(self) -> None:
        device = make_device()

        # Ack of the auto-on command
        await device.handle_cmd_hdu_from_estia({"data": "ff" * 10 + "01" + "ff" * 6 + "0120" + "ff" * 16})

        self.assertTrue(device.auto_mode)
        self.assertTrue(device.function_is_on(EstiaFunctionFlag.ANTI_BACTERIAL))

    async def test_setpoints_outside_the_limits_are_refused(self) -> None:
        device = make_device()

        for setter, value in (
            (device.set_dhw_target_temperature, 76),
            (device.set_dhw_target_temperature, 39),
            (device.set_zone1_target_temperature, 66),
            (device.set_zone2_target_temperature, 19),
        ):
            with self.subTest(setter=setter.__name__, value=value):
                with self.assertRaises(ToshibaEstiaDeviceError):
                    await setter(value)

        device.http_api.send_command.assert_not_awaited()

    async def test_limits_come_from_the_device_record(self) -> None:
        device = make_device()
        device.http_api.get_estia_device_additional_info = AsyncMock(
            return_value=await _additional_info({"DHW_max": "a0", "DHW_min": "70"})
        )

        await device.load_additional_device_info()

        self.assertEqual(device.dhw_max_temperature, 64.0)
        self.assertEqual(device.dhw_min_temperature, 40.0)
        # Not in the record: the defaults
        self.assertEqual(device.zone1_max_temperature, 65.0)


async def _additional_info(record: dict):
    api = ToshibaAcHttpApi("user", "password", "0123456789abcdef")
    api.request_api = AsyncMock(return_value=record)  # type: ignore[method-assign]
    return await api.get_estia_device_additional_info("unique-id")


class EstiaLimitsParsingTest(unittest.IsolatedAsyncioTestCase):
    async def test_parses_the_captured_limits(self) -> None:
        info = await _additional_info(
            {
                "Fcu": {"model_name": "1101XWHT6W-E"},
                "DHW_max": "b6",
                "DHW_min": "70",
                "Zone1_wt_heat_max": "a2",
                "Zone1_wt_heat_min": "48",
                "Zone2_wt_heat_max": "a2",
                "Zone2_wt_heat_min": "48",
            }
        )

        self.assertEqual((info.limits.dhw_min, info.limits.dhw_max), (40.0, 75.0))
        self.assertEqual((info.limits.zone1_heat_min, info.limits.zone1_heat_max), (20.0, 65.0))
        self.assertEqual((info.limits.zone2_heat_min, info.limits.zone2_heat_max), (20.0, 65.0))

    async def test_finds_limits_in_a_nested_object(self) -> None:
        info = await _additional_info({"Fcu": {"model_name": "x", "DHW_max": "b6"}})

        self.assertEqual(info.limits.dhw_max, 75.0)

    async def test_missing_or_junk_limits_are_none(self) -> None:
        info = await _additional_info({"DHW_max": "zz", "DHW_min": None, "Zone1_wt_heat_max": "ff"})

        self.assertIsNone(info.limits.dhw_max)
        self.assertIsNone(info.limits.dhw_min)
        self.assertIsNone(info.limits.zone1_heat_max)
        self.assertIsNone(info.limits.zone2_heat_min)


class FakeResponse:
    def __init__(self, status: int, json: dict) -> None:
        self.status = status
        self._json = json
        self.headers: dict = {}

    async def json(self) -> dict:
        return self._json

    async def text(self) -> str:
        return str(self._json)

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args) -> None:
        return None


class FakeSession:
    """Stands in for aiohttp.ClientSession and records every request."""

    closed = False

    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict]] = []

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.requests.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.requests.append(("GET", url, kwargs))
        return self.responses.pop(0)


SUCCESS = {
    "ResObj": {"Status": "Success", "FailedTargetIds": [], "UnauthorizedTargetIds": []},
    "IsSuccess": True,
    "Message": "Success",
    "StatusCode": "Success",
}


def make_http_api(*responses: FakeResponse) -> tuple[ToshibaAcHttpApi, FakeSession]:
    api = ToshibaAcHttpApi(
        "user",
        "password",
        "0123456789abcdef",
        access_token="token",
        access_token_type="Bearer",
        consumer_id="consumer",
    )
    api.REQUEST_MIN_INTERVAL_S = 0
    api.REQUEST_JITTER_S = 0
    session = FakeSession(*responses)
    api.session = session  # type: ignore[assignment]
    api._message_counter = itertools.count(42)
    return api, session


class EstiaSendCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_posts_the_app_envelope(self) -> None:
        api, session = make_http_api(FakeResponse(200, SUCCESS))

        await api.send_command(SOURCE_ID, "unique-id", "CMD_HDU_TO_ESTIA", "ff7cffffffffffffffffffffffff")

        self.assertEqual(len(session.requests), 1)
        method, url, kwargs = session.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://mobileapi.toshibahomeaccontrols.com/api/IoTHub/SendCommand")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")

        body = kwargs["json"]
        time_stamp = body.pop("timeStamp")
        self.assertRegex(time_stamp, r"^\d{2}:\d{2}:\d{2}\.\d{7}$")
        self.assertEqual(
            body,
            {
                "messageId": "MB_user@example.com_0-00000042",
                "sourceId": SOURCE_ID,
                "targetId": ["unique-id"],
                "cmd": "CMD_HDU_TO_ESTIA",
                "payload": {"data": "ff7cffffffffffffffffffffffff"},
            },
        )

    async def test_message_ids_count_up(self) -> None:
        api, session = make_http_api(FakeResponse(200, SUCCESS), FakeResponse(200, SUCCESS))

        await api.send_command(SOURCE_ID, "unique-id", "CMD_HDU_TO_ESTIA", "ff" * 14)
        await api.send_command(SOURCE_ID, "unique-id", "CMD_HDU_TO_ESTIA", "ff" * 14)

        ids = [kwargs["json"]["messageId"] for _, _, kwargs in session.requests]
        self.assertEqual([re.sub(r".*-", "", i) for i in ids], ["00000042", "00000043"])

    async def test_a_device_command_goes_out_through_send_command(self) -> None:
        api, session = make_http_api(FakeResponse(200, SUCCESS))
        device = make_device()
        device.http_api = api

        await device.set_dhw_target_temperature(45)

        self.assertEqual(session.requests[0][2]["json"]["payload"], {"data": "ff7affffffffffffffffffffffff"})
        self.assertEqual(session.requests[0][2]["json"]["sourceId"], SOURCE_ID)

    async def test_failed_or_unauthorized_targets_raise(self) -> None:
        for res_obj in (
            {"Status": "Success", "FailedTargetIds": ["unique-id"], "UnauthorizedTargetIds": []},
            {"Status": "Success", "FailedTargetIds": [], "UnauthorizedTargetIds": ["unique-id"]},
            {"Status": "Failed", "FailedTargetIds": [], "UnauthorizedTargetIds": []},
        ):
            with self.subTest(res_obj=res_obj):
                api, _ = make_http_api(FakeResponse(200, {**SUCCESS, "ResObj": res_obj}))

                with self.assertRaises(ToshibaAcHttpApiError):
                    await api.send_command(SOURCE_ID, "unique-id", "CMD_HDU_TO_ESTIA", "ff" * 14)

    async def test_rate_limit_is_not_retried_for_minutes(self) -> None:
        # A command that lands minutes late is worse than a visible failure
        api, session = make_http_api(FakeResponse(429, {}))

        with self.assertRaises(ToshibaAcHttpApiRateLimitError):
            await api.send_command(SOURCE_ID, "unique-id", "CMD_HDU_TO_ESTIA", "ff" * 14)

        self.assertEqual(len(session.requests), 1)


if __name__ == "__main__":
    unittest.main()
