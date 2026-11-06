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

import datetime
import asyncio
import itertools
import logging
import random
import secrets
import typing as t
from dataclasses import dataclass, field

import aiohttp
from ..device.properties import EstiaSetpointLimits, EstiaWaterTemperatures, ToshibaAcDeviceEnergyConsumption
from ..utils import RetryJitterMode, retry_on_exception

logger = logging.getLogger(__name__)

# Toshiba's WAF answers with 403, and its login rate limiter with 429. Both mean
# "back off", so both must raise ToshibaAcHttpApiRateLimitError to reach the slow
# rate-limit retry policy rather than the fast one meant for transient errors.
RATE_LIMIT_STATUSES = frozenset({403, 429})


@dataclass
class ToshibaAcDeviceInfo:
    ac_id: str
    ac_unique_id: str
    ac_name: str
    initial_ac_state: str
    firmware_version: str
    merit_feature: str
    ac_model_id: str


@dataclass
class ToshibaAcDeviceAdditionalInfo:
    cdu: t.Optional[str]
    fcu: t.Optional[str]


@dataclass
class ToshibaEstiaAdditionalInfo:
    cdu: t.Optional[str]
    fcu: t.Optional[str]
    serial_number: t.Optional[str]
    temperatures: EstiaWaterTemperatures
    limits: EstiaSetpointLimits = field(default_factory=EstiaSetpointLimits)


@dataclass
class ToshibaDeviceConnectionState:
    device_id: str
    online: bool


def _find_field(res: t.Any, key: str, depth: int = 2) -> t.Any:
    """Look a field up at the top level of a response, then in its nested objects.

    The Estia setpoint limits are known by name only, not by where in the device
    record Toshiba puts them, so they are searched for a couple of levels deep.
    """
    if not isinstance(res, dict):
        return None

    if key in res:
        return res[key]

    if depth <= 0:
        return None

    for value in res.values():
        found = _find_field(value, key, depth - 1)
        if found is not None:
            return found

    return None


def _parse_estia_limit(res: t.Any, key: str) -> t.Optional[float]:
    """Read a setpoint limit, encoded like every Estia setpoint: T * 2 + 32."""
    value = _find_field(res, key)

    try:
        raw = value if isinstance(value, int) and not isinstance(value, bool) else int(value, 16)
    except (TypeError, ValueError):
        return None

    if not 0 <= raw < 0xFF:
        return None

    return (raw - 32) / 2


def _parse_hex_field(res: t.Any, key: str) -> t.Optional[int]:
    """Read a hex-string field from an API response, tolerating absence and junk."""
    try:
        return int(res[key], 16)
    except (KeyError, TypeError, ValueError):
        return None


class ToshibaAcHttpApiError(Exception):
    pass


class ToshibaAcHttpApiAuthError(ToshibaAcHttpApiError):
    pass


class ToshibaAcHttpApiRateLimitError(ToshibaAcHttpApiError):
    pass


class ToshibaAcHttpApi:
    REQUEST_MIN_INTERVAL_S = 0.15
    REQUEST_JITTER_S = 0.25
    BASE_URL = "https://mobileapi.toshibahomeaccontrols.com"
    LOGIN_PATH = "/api/Consumer/Login"
    REGISTER_PATH = "/api/Consumer/RegisterMobileDevice"
    AC_MAPPING_PATH = "/api/AC/GetConsumerACMapping"
    AC_STATE_PATH = "/api/AC/GetCurrentACState"
    AC_ENERGY_CONSUMPTION_PATH = "/api/AC/GetGroupACEnergyConsumption"
    ESTIA_MAPPING_PATH = "/api/Estia/GetConsumerEstiaMapping"
    ESTIA_STATE_PATH = "/api/Estia/GetCurrentEstiaStateByUniqueDeviceId"
    ALL_DEVICE_STATE_PATH = "/api/AC/GetAllDeviceState"
    SEND_COMMAND_PATH = "/api/IoTHub/SendCommand"

    def __init__(
        self,
        username: str,
        password: str,
        device_id: t.Optional[str] = None,
        access_token: t.Optional[str] = None,
        access_token_type: t.Optional[str] = None,
        consumer_id: t.Optional[str] = None,
    ) -> None:
        self.username = username
        self.password = password
        self.device_id = device_id or secrets.token_hex(8)
        self.access_token: t.Optional[str] = access_token
        self.access_token_type: t.Optional[str] = access_token_type
        self.consumer_id: t.Optional[str] = consumer_id
        # Called with (access_token, access_token_type, consumer_id) whenever a login
        # produces a new session, so a consumer can persist it and skip the next login.
        self.on_access_token_updated: t.Optional[t.Callable[[str, str, str], t.Awaitable[None]]] = None
        self.session: t.Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._auth_generation = 0
        self._request_pacing_lock = asyncio.Lock()
        self._next_request_not_before = 0.0
        # The app numbers its commands with an 8-digit counter. Start at a random
        # point so that a restart does not reuse message ids of the last session.
        self._message_counter = itertools.count(random.randrange(10_000_000))

    async def _pace_requests(self) -> None:
        async with self._request_pacing_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()

            wait_for = max(0.0, self._next_request_not_before - now)
            if wait_for > 0:
                await asyncio.sleep(wait_for)

            self._next_request_not_before = (
                loop.time() + self.REQUEST_MIN_INTERVAL_S + random.uniform(0.0, self.REQUEST_JITTER_S)
            )

    async def _ensure_session(self) -> None:
        async with self._session_lock:
            if not self.session or self.session.closed:
                timeout = aiohttp.ClientTimeout(total=20, connect=10, sock_read=15)
                # Toshiba's app sends its stable Android ID with every request.
                # Toshiba's energy endpoint rejects the browser-like User-Agent
                # previously used by this library with HTTP 403. An empty value also
                # prevents aiohttp from injecting its default User-Agent.
                self.session = aiohttp.ClientSession(
                    timeout=timeout,
                    headers={"Device-ID": self.device_id, "User-Agent": ""},
                )

    async def _refresh_auth_if_stale(self, failed_auth_generation: int) -> None:
        async with self._auth_lock:
            if self._auth_generation != failed_auth_generation:
                return

            await self.connect()

    @retry_on_exception(
        exceptions=ToshibaAcHttpApiRateLimitError,
        retries=5,
        backoff=10,
        max_backoff=600,
        growth_factor=3,
        jitter_mode=RetryJitterMode.EQUAL,
    )
    @retry_on_exception(
        exceptions=(ToshibaAcHttpApiError, aiohttp.ClientError, asyncio.TimeoutError),
        retries=2,
        backoff=5,
        max_backoff=30,
        should_retry=lambda e: not isinstance(
            e,
            (ToshibaAcHttpApiAuthError, ToshibaAcHttpApiRateLimitError),
        ),
    )
    async def request_api(
        self,
        path: str,
        get: dict[str, str] | None = None,
        # Most endpoints take a JSON object, but GetAllDeviceState takes a bare
        # JSON array of device ids
        post: t.Mapping[str, t.Any] | t.Sequence[str] | None = None,
        headers: t.Any = None,
        reauth_on_auth_error: bool = True,
    ) -> t.Any:
        auth_generation = self._auth_generation
        is_authenticated_request = False

        if not isinstance(headers, dict):
            if not self.access_token_type or not self.access_token:
                raise ToshibaAcHttpApiError("Failed to send request, missing access token")

            headers = {
                "Content-Type": "application/json",
                "Authorization": self.access_token_type + " " + self.access_token,
            }
            is_authenticated_request = True

        url = self.BASE_URL + path

        await self._ensure_session()

        if not self.session:
            raise ToshibaAcHttpApiError("Failed to initialize HTTP session")

        await self._pace_requests()

        method_args = {"params": get, "headers": headers}

        if post:
            logger.debug(f"Sending POST to {url}")
            method_args["json"] = post
            method = self.session.post
        else:
            logger.debug(f"Sending GET to {url}")
            method = self.session.get

        async with method(url, **method_args) as response:
            logger.debug(f"Response code: {response.status}")

            if response.status == 200:
                try:
                    json = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as e:
                    raise ToshibaAcHttpApiError(f"Malformed JSON response for {path}: {e}") from e

                if json["IsSuccess"]:
                    return json["ResObj"]
                else:
                    message = json.get("Message") or ""
                    # Toshiba reports bad credentials under more than one StatusCode
                    # (a wrong password with a valid username does NOT come back as
                    # InvalidUserNameorPassword), so also match the message text.
                    # Auth errors must never be retried: Toshiba counts every failed
                    # login toward an account lockout. The message heuristic applies
                    # to the login endpoint only - on data endpoints "lock" would
                    # match transient messages like "blocked", turning a retryable
                    # hiccup into a credentials failure.
                    if json["StatusCode"] == "InvalidUserNameorPassword" or (
                        path == self.LOGIN_PATH and any(marker in message.lower() for marker in ("password", "lock"))
                    ):
                        raise ToshibaAcHttpApiAuthError(message)

                    raise ToshibaAcHttpApiError(message)

            response_text = await response.text()
            # 403 and 429 are Toshiba's WAF/rate-limit — expected and retried by the decorator
            # above, and AMQP push keeps state fresh regardless, so keep them out of the default
            # WARNING log.
            level = logging.INFO if response.status in RATE_LIMIT_STATUSES else logging.WARNING
            logger.log(
                level,
                "Non-200 response from Toshiba API "
                f"(status={response.status}, path={path}, content_type={response.headers.get('Content-Type')}, "
                f"server={response.headers.get('Server')})",
            )
            logger.debug(f"Non-200 response body for {path} (first 500 chars): {response_text[:500]}")

            if is_authenticated_request and response.status == 401:
                if reauth_on_auth_error:
                    logger.warning(
                        f"Auth failed for endpoint {path} with status 401. " f"Refreshing auth and retrying once."
                    )
                    await self._refresh_auth_if_stale(auth_generation)
                    return await self.request_api(
                        path,
                        get=get,
                        post=post,
                        headers=None,
                        reauth_on_auth_error=False,
                    )

                raise ToshibaAcHttpApiAuthError(f"HTTP 401 calling {path}")

            if response.status in RATE_LIMIT_STATUSES:
                raise ToshibaAcHttpApiRateLimitError(f"HTTP {response.status} calling {path}")

            raise ToshibaAcHttpApiError(f"HTTP {response.status} calling {path}")

    async def connect(self) -> None:
        headers = {
            "Content-Type": "application/json",
        }
        post = {"Username": self.username, "Password": self.password}

        res = await self.request_api(self.LOGIN_PATH, post=post, headers=headers)

        self.access_token = res["access_token"]
        self.access_token_type = res["token_type"]
        self.consumer_id = res["consumerId"]
        self._auth_generation += 1

        if self.on_access_token_updated and self.access_token and self.access_token_type and self.consumer_id:
            await self.on_access_token_updated(self.access_token, self.access_token_type, self.consumer_id)

    async def shutdown(self) -> None:
        async with self._session_lock:
            if self.session:
                await self.session.close()
                self.session = None

    async def get_devices(self) -> t.List[ToshibaAcDeviceInfo]:
        return await self._get_devices_from_mapping(self.AC_MAPPING_PATH)

    async def get_estia_devices(self) -> t.List[ToshibaAcDeviceInfo]:
        """Return the Estia heat pumps on the account.

        Estia units are kept in a mapping of their own, so an account can hold
        both kinds and neither mapping lists the other's devices.
        """
        return await self._get_devices_from_mapping(self.ESTIA_MAPPING_PATH)

    async def _get_devices_from_mapping(self, path: str) -> t.List[ToshibaAcDeviceInfo]:
        if not self.consumer_id:
            raise ToshibaAcHttpApiError("Failed to send request, missing consumer id")

        get = {"consumerId": self.consumer_id}

        res = await self.request_api(path, get=get)

        devices = []

        for group in res or []:
            for device in group.get("ACList") or []:
                devices.append(
                    ToshibaAcDeviceInfo(
                        device["Id"],
                        device["DeviceUniqueId"],
                        device["Name"],
                        device["ACStateData"],
                        device["FirmwareVersion"],
                        device["MeritFeature"],
                        device["ACModelId"],
                    )
                )

        return devices

    async def get_device_state(self, ac_id: str) -> str:
        get = {
            "ACId": ac_id,
        }
        if self.consumer_id:
            get["consumerId"] = self.consumer_id

        res = await self.request_api(self.AC_STATE_PATH, get=get)

        if "ACStateData" not in res:
            raise ToshibaAcHttpApiError("Missing ACStateData in response")

        if not isinstance(res["ACStateData"], str):
            raise ToshibaAcHttpApiError("Malformed ACStateData in response")

        return res["ACStateData"]

    async def get_device_additional_info(self, ac_id: str) -> ToshibaAcDeviceAdditionalInfo:
        get = {
            "ACId": ac_id,
        }
        if self.consumer_id:
            get["consumerId"] = self.consumer_id

        res = await self.request_api(self.AC_STATE_PATH, get=get)

        try:
            cdu = res["Cdu"]["model_name"]
        except (KeyError, TypeError):
            cdu = None

        try:
            fcu = res["Fcu"]["model_name"]
        except (KeyError, TypeError):
            fcu = None

        return ToshibaAcDeviceAdditionalInfo(cdu=cdu, fcu=fcu)

    async def get_estia_device_state(self, device_unique_id: str) -> str:
        get = {
            "deviceuniqueId": device_unique_id,
        }

        logger.debug(f"Requesting Estia state for device_unique_id: {device_unique_id}")

        res = await self.request_api(self.ESTIA_STATE_PATH, get=get)

        if "ACStateData" not in res:
            raise ToshibaAcHttpApiError("Missing ACStateData in response")

        if not isinstance(res["ACStateData"], str):
            raise ToshibaAcHttpApiError("Malformed ACStateData in response")

        return res["ACStateData"]

    async def get_estia_device_additional_info(self, device_unique_id: str) -> ToshibaEstiaAdditionalInfo:
        """Return model identification plus the current water circuit readings.

        Same endpoint as get_estia_device_state - Toshiba returns the sensor values
        alongside the status string, which is the only way to read them without the
        AMQP heartbeat.
        """
        get = {
            "deviceuniqueId": device_unique_id,
        }

        logger.debug(f"Requesting Estia extended info for device_unique_id: {device_unique_id}")

        res = await self.request_api(self.ESTIA_STATE_PATH, get=get)

        try:
            cdu = res["Cdu"]["model_name"]
        except (KeyError, TypeError):
            cdu = None

        try:
            fcu = res["Fcu"]["model_name"]
        except (KeyError, TypeError):
            fcu = None

        try:
            serial_number = res["Fcu"]["serial_number"]
        except (KeyError, TypeError):
            serial_number = None

        temperatures = EstiaWaterTemperatures(
            two=_parse_hex_field(res, "TWO_Temp"),
            twi=_parse_hex_field(res, "TWI_Temp"),
            tho=_parse_hex_field(res, "THO_Temp"),
            to=_parse_hex_field(res, "TO_Temp"),
            tfi=_parse_hex_field(res, "TFI_Temp"),
            room_water=_parse_hex_field(res, "RoomWater_temp"),
        )

        limits = EstiaSetpointLimits(
            dhw_min=_parse_estia_limit(res, "DHW_min"),
            dhw_max=_parse_estia_limit(res, "DHW_max"),
            zone1_heat_min=_parse_estia_limit(res, "Zone1_wt_heat_min"),
            zone1_heat_max=_parse_estia_limit(res, "Zone1_wt_heat_max"),
            zone2_heat_min=_parse_estia_limit(res, "Zone2_wt_heat_min"),
            zone2_heat_max=_parse_estia_limit(res, "Zone2_wt_heat_max"),
        )

        return ToshibaEstiaAdditionalInfo(
            cdu=cdu,
            fcu=fcu,
            serial_number=serial_number,
            temperatures=temperatures,
            limits=limits,
        )

    async def send_command(self, source_id: str, target_id: str, cmd: str, data: str) -> None:
        """Send a command to a device through Toshiba's REST command endpoint.

        This is how the Toshiba mobile app sends commands to an Estia unit; the
        envelope is the same one AC commands use over AMQP. The unit acknowledges
        with a status diff carrying the same messageId, which arrives as a normal
        state push and is merged like any other.
        """
        # The app's message ids look like "MB_<first 18 chars of sourceId>-<8 digits>"
        message_id = f"MB_{source_id[:18]}-{next(self._message_counter) % 100_000_000:08d}"
        # The app sends the UTC time of day with seven fractional digits
        time_stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S.%f") + "0"

        post = {
            "timeStamp": time_stamp,
            "messageId": message_id,
            "sourceId": source_id,
            "targetId": [target_id],
            "cmd": cmd,
            "payload": {"data": data},
        }

        logger.debug(f"Sending {cmd} {data} to {target_id} as {message_id}")

        # Skip the outer rate-limit retry, whose backoff runs to minutes: a
        # setpoint arriving that late is worse than a visible failure. Transient
        # errors still get the short retry; every command is idempotent.
        res = await ToshibaAcHttpApi.request_api.__wrapped__(self, self.SEND_COMMAND_PATH, post=post)  # type: ignore[attr-defined]

        if not isinstance(res, dict):
            raise ToshibaAcHttpApiError(f"Malformed response to {cmd}: {res!r}")

        failed = res.get("FailedTargetIds") or []
        unauthorized = res.get("UnauthorizedTargetIds") or []
        status = res.get("Status")

        if unauthorized:
            raise ToshibaAcHttpApiError(f"{cmd} rejected, not authorized for {unauthorized}")

        if failed or not isinstance(status, str) or status.lower() != "success":
            raise ToshibaAcHttpApiError(f"{cmd} failed (status {status!r}, failed targets {failed})")

    async def get_device_connection_state(self, device_ids: t.List[str]) -> t.Dict[str, ToshibaDeviceConnectionState]:
        """Return whether each device is currently connected to the Toshiba cloud.

        Estia units have no equivalent of the AC "status" byte, so this endpoint is
        the only way to tell a heat pump that is off from one that fell off the net.
        """
        if not device_ids:
            return {}

        logger.debug(f"Requesting connection states for device_ids: {device_ids}")

        res = await self.request_api(self.ALL_DEVICE_STATE_PATH, post=device_ids)

        if not isinstance(res, list):
            raise ToshibaAcHttpApiError("Expected ResObj to be a list")

        connection_states = {}

        for device_state in res:
            try:
                device_id = device_state["DeviceId"]
                online = device_state["ConnectionState"] == "Connected"
            except (KeyError, TypeError) as e:
                raise ToshibaAcHttpApiError(f"Malformed device state in response: {e}") from e

            connection_states[device_id] = ToshibaDeviceConnectionState(device_id=device_id, online=online)

        logger.debug(f"Connection states: {connection_states}")

        return connection_states

    async def get_devices_energy_consumption(
        self, ac_unique_ids: t.List[str], is_estia: bool = False
    ) -> t.Dict[str, ToshibaAcDeviceEnergyConsumption]:
        year = int(datetime.datetime.now().year)
        since = datetime.datetime(year, 1, 1).astimezone(datetime.timezone.utc)

        post: t.Dict[str, t.Any] = {
            "ACDeviceUniqueIdList": ac_unique_ids,
            "FromUtcTime": str(year),
            "Timezone": "UTC",
            "ToUtcTime": str(year + 1),
            "Type": "EnergyYear",
        }

        if is_estia:
            # Without the flag the endpoint answers with empty consumption for a heat pump
            post["IsEstia"] = True

        res = await self.request_api(self.AC_ENERGY_CONSUMPTION_PATH, post=post)

        ret = {}

        for ac in res or []:
            try:
                energy_consumption = ac["EnergyConsumption"]
                if not energy_consumption:
                    continue

                consumption = sum(int(item["Energy"]) for item in energy_consumption)
                ret[ac["ACDeviceUniqueId"]] = ToshibaAcDeviceEnergyConsumption(consumption, since)
            except (KeyError, TypeError, ValueError):
                pass

        return ret

    async def register_client(self, device_id: str) -> str:
        post = {"DeviceID": device_id, "DeviceType": "1", "Username": self.username}

        res = await self.request_api(self.REGISTER_PATH, post=post)

        if "SasToken" not in res:
            raise ToshibaAcHttpApiError("Missing SasToken in response")

        if not isinstance(res["SasToken"], str):
            raise ToshibaAcHttpApiError("Malformed SasToken in response")

        return res["SasToken"]
