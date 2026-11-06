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

import asyncio
import logging
import typing as t

from .device import ToshibaAcDevice
from .device.estia import ToshibaEstiaDevice
from .utils import async_sleep_until_next_multiply_of_minutes, ToshibaAcCallback
from .utils.amqp_api import ToshibaAcAmqpApi, JSONSerializable
from .utils.http_api import ToshibaAcDeviceInfo, ToshibaAcHttpApi

logger = logging.getLogger(__name__)


class ToshibaAcDeviceManagerError(Exception):
    pass


class ToshibaAcSasTokenUpdatedCallback(ToshibaAcCallback[str]):
    pass


class ToshibaAcAccessTokenUpdatedCallback(ToshibaAcCallback[t.Tuple[str, str, str]]):
    """Fired with (access_token, access_token_type, consumer_id) after a successful login."""


class ToshibaAcDeviceManager:
    # Energy is a year-to-date cumulative total, and Home Assistant aggregates it into
    # hourly statistics, so polling more often than hourly buys no resolution - it only
    # multiplies our request footprint on Toshiba's rate-limited API.
    FETCH_ENERGY_CONSUMPTION_PERIOD_MINUTES = 60
    # Only Estia devices need this: an AC reports its own on/off status, a heat pump
    # does not, so cloud connectivity is the only availability signal it has.
    FETCH_CONNECTION_STATE_PERIOD_MINUTES = 60

    def __init__(
        self,
        username: str,
        password: str,
        device_id: t.Optional[str] = None,
        sas_token: t.Optional[str] = None,
        access_token: t.Optional[str] = None,
        access_token_type: t.Optional[str] = None,
        consumer_id: t.Optional[str] = None,
    ):
        self.username = username
        self.password = password
        self.http_api: t.Optional[ToshibaAcHttpApi] = None
        self.reg_info = None
        self.amqp_api: t.Optional[ToshibaAcAmqpApi] = None
        self.http_device_id = device_id or "3e6e4eb5f0e5aa46"
        self.device_id = self.username + "_" + self.http_device_id
        self.sas_token = sas_token
        self.access_token = access_token
        self.access_token_type = access_token_type
        self.consumer_id = consumer_id
        self.devices: t.Dict[str, ToshibaAcDevice] = {}
        self.estia_devices: t.Dict[str, ToshibaEstiaDevice] = {}
        self._devices_discovered = False
        self.periodic_fetch_energy_consumption_task: t.Optional[asyncio.Task[None]] = None
        self.periodic_fetch_connection_state_task: t.Optional[asyncio.Task[None]] = None
        self.lock = asyncio.Lock()
        self.loop = asyncio.get_running_loop()
        self._on_sas_token_updated_callback = ToshibaAcSasTokenUpdatedCallback()
        self._on_access_token_updated_callback = ToshibaAcAccessTokenUpdatedCallback()

    async def connect(self) -> str:
        try:
            async with self.lock:
                if not self.http_api:
                    self.http_api = ToshibaAcHttpApi(
                        self.username,
                        self.password,
                        self.http_device_id,
                        access_token=self.access_token,
                        access_token_type=self.access_token_type,
                        consumer_id=self.consumer_id,
                    )
                    self.http_api.on_access_token_updated = self._handle_access_token_updated

                    # A cached session skips /api/Consumer/Login, which is the most
                    # aggressively rate-limited endpoint on this API. If the token has
                    # expired, request_api reauthenticates on the first 401 and retries.
                    if self.http_api.access_token and self.http_api.consumer_id:
                        logger.debug("Reusing cached access token, skipping login")
                    else:
                        await self.http_api.connect()

                if not self.sas_token:
                    self.sas_token = await self.http_api.register_client(self.device_id)

                if not self.amqp_api:
                    self.amqp_api = ToshibaAcAmqpApi(self.sas_token, self.renew_sas_token)
                    self.amqp_api.register_command_handler("CMD_FCU_FROM_AC", self.handle_cmd_fcu_from_ac)
                    self.amqp_api.register_command_handler("CMD_HEARTBEAT", self.handle_cmd_heartbeat)
                    self.amqp_api.register_command_handler("CMD_HDU_FROM_ESTIA", self.handle_cmd_hdu_from_estia)
                    self.amqp_api.register_command_handler("CMD_HEARTBEAT_ESTIA", self.handle_cmd_heartbeat_estia)
                    await self.amqp_api.connect()

                return self.sas_token

        except:
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        async with self.lock:
            tasks: t.List[t.Awaitable[None]] = []

            if self.periodic_fetch_energy_consumption_task:
                self.periodic_fetch_energy_consumption_task.cancel()
                tasks.append(self.periodic_fetch_energy_consumption_task)

            if self.periodic_fetch_connection_state_task:
                self.periodic_fetch_connection_state_task.cancel()
                tasks.append(self.periodic_fetch_connection_state_task)

            tasks.extend(device.shutdown() for device in self.devices.values())
            tasks.extend(device.shutdown() for device in self.estia_devices.values())

            if self.amqp_api:
                tasks.append(self.amqp_api.shutdown())

            if self.http_api:
                tasks.append(self.http_api.shutdown())

            try:
                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    def raise_all_errors(res: t.Any, *args: t.Any) -> None:
                        try:
                            if isinstance(res, Exception):
                                raise res
                        finally:
                            if args:
                                raise_all_errors(*args)

                    raise_all_errors(*results)
            finally:
                self.periodic_fetch_energy_consumption_task = None
                self.periodic_fetch_connection_state_task = None
                self.amqp_api = None
                self.http_api = None

    async def periodic_fetch_energy_consumption(self) -> None:
        while True:
            try:
                await self.fetch_energy_consumption()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Best-effort periodic poll; not an error when it eventually recovers
                logger.warning(f"Fetching energy consumption failed: {e}")
                pass

            await async_sleep_until_next_multiply_of_minutes(self.FETCH_ENERGY_CONSUMPTION_PERIOD_MINUTES)

    async def fetch_energy_consumption(self) -> None:
        if not self.http_api:
            raise ToshibaAcDeviceManagerError("Not connected")

        # The endpoint needs to be told which kind of device it is being asked
        # about, so AC units and heat pumps cannot share one request.
        await self._fetch_energy_consumption_for(self.devices, is_estia=False)
        await self._fetch_energy_consumption_for(self.estia_devices, is_estia=True)

    async def _fetch_energy_consumption_for(
        self,
        devices: t.Mapping[str, t.Any],
        is_estia: bool,
    ) -> None:
        if not devices or not self.http_api:
            return

        consumptions = await self.http_api.get_devices_energy_consumption(list(devices.keys()), is_estia=is_estia)

        logger.debug(
            "Power consumption for devices: {"
            + " ,".join(
                f"{devices[ac_unique_id].name}: {consumption.energy_wh}Wh"
                for ac_unique_id, consumption in consumptions.items()
                if ac_unique_id in devices
            )
            + "}"
        )

        updates = []

        for ac_unique_id, consumption in consumptions.items():
            device = devices.get(ac_unique_id)

            if not device:
                continue

            updates.append(device.handle_update_ac_energy_consumption(consumption))

        await asyncio.gather(*updates)

    async def periodic_fetch_connection_state(self) -> None:
        # Discovery already did the first fetch, so that entities know their
        # availability before they are handed to the consumer
        while True:
            await async_sleep_until_next_multiply_of_minutes(self.FETCH_CONNECTION_STATE_PERIOD_MINUTES)

            try:
                await self.fetch_connection_state()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Best-effort periodic poll; a failure only delays an availability change
                logger.warning(f"Fetching device connection state failed: {e}")

    async def fetch_connection_state(self) -> None:
        if not self.http_api:
            raise ToshibaAcDeviceManagerError("Not connected")

        if not self.estia_devices:
            return

        connection_states = await self.http_api.get_device_connection_state(list(self.estia_devices.keys()))

        updates = []

        for ac_unique_id, connection_state in connection_states.items():
            device = self.estia_devices.get(ac_unique_id)

            if not device:
                continue

            updates.append(device.handle_connection_state(connection_state.online))

        await asyncio.gather(*updates)

    async def get_devices(self) -> t.List[ToshibaAcDevice]:
        """Return the AC units on the account.

        Heat pumps are deliberately not included: they share nothing but the
        connection, and every consumer of this list expects AC attributes.
        """
        await self._discover_devices()

        return list(self.devices.values())

    async def get_estia_devices(self) -> t.List[ToshibaEstiaDevice]:
        """Return the Estia heat pumps on the account."""
        await self._discover_devices()

        return list(self.estia_devices.values())

    async def _discover_devices(self) -> None:
        if not self.http_api or not self.amqp_api:
            raise ToshibaAcDeviceManagerError("Not connected")

        async with self.lock:
            if self._devices_discovered:
                return

            devices_info = await self.http_api.get_devices()
            estia_devices_info = await self._get_estia_devices_info()

            self._log_found_devices("AC", devices_info)
            self._log_found_devices("Estia", estia_devices_info)

            connects = []

            for device_info in devices_info:
                device = ToshibaAcDevice(
                    device_info.ac_name,
                    self.device_id,
                    device_info.ac_id,
                    device_info.ac_unique_id,
                    device_info.initial_ac_state,
                    device_info.firmware_version,
                    device_info.merit_feature,
                    device_info.ac_model_id,
                    self.amqp_api,
                    self.http_api,
                )

                connects.append(device.connect())

                logger.debug(f"Adding AC device {device.name}")

                self.devices[device.ac_unique_id] = device

            for device_info in estia_devices_info:
                estia_device = ToshibaEstiaDevice(
                    device_info.ac_name,
                    self.device_id,
                    device_info.ac_id,
                    device_info.ac_unique_id,
                    device_info.initial_ac_state,
                    device_info.firmware_version,
                    device_info.merit_feature,
                    device_info.ac_model_id,
                    self.amqp_api,
                    self.http_api,
                )

                connects.append(estia_device.connect())

                logger.debug(f"Adding Estia device {estia_device.name}")

                self.estia_devices[estia_device.ac_unique_id] = estia_device

            await asyncio.gather(*connects)

            self._devices_discovered = True

            energy_reported = any(device.supported.ac_energy_report for device in self.devices.values())

            if (energy_reported or self.estia_devices) and not self.periodic_fetch_energy_consumption_task:
                self.periodic_fetch_energy_consumption_task = asyncio.create_task(
                    self.periodic_fetch_energy_consumption()
                )

            if self.estia_devices:
                # Before the caller builds anything on top of these devices, so a heat
                # pump does not present itself as unavailable for the first hour
                try:
                    await self.fetch_connection_state()
                except Exception as e:
                    logger.warning(f"Fetching initial device connection state failed: {e}")

                if not self.periodic_fetch_connection_state_task:
                    self.periodic_fetch_connection_state_task = asyncio.create_task(
                        self.periodic_fetch_connection_state()
                    )

    async def _get_estia_devices_info(self) -> t.List[ToshibaAcDeviceInfo]:
        """Look up Estia heat pumps, tolerating accounts and regions without them.

        The vast majority of accounts hold only AC units, and a failing Estia
        mapping must never take the AC setup down with it.
        """
        if not self.http_api:
            raise ToshibaAcDeviceManagerError("Not connected")

        try:
            return await self.http_api.get_estia_devices()
        except Exception as e:
            logger.info(f"Could not list Estia devices, continuing without them: {e}")
            return []

    @staticmethod
    def _log_found_devices(kind: str, devices_info: t.List[ToshibaAcDeviceInfo]) -> None:
        if not devices_info:
            return

        logger.debug(
            f"Found {kind} devices: {{"
            + " ,".join(
                f"{device.ac_name}: {{MeritFeature: {device.merit_feature}, "
                + f"Model id: {device.ac_model_id}, "
                + f"Firmware version: {device.firmware_version}, "
                + f"Initial state: {device.initial_ac_state}}}"
                for device in devices_info
            )
            + "}"
        )

    async def renew_sas_token(self) -> str:
        if self.http_api:
            self.sas_token = await self.http_api.register_client(self.device_id)
            await self.on_sas_token_updated_callback(self.sas_token)
            return self.sas_token

        raise ToshibaAcDeviceManagerError("Not connected")

    def _schedule_device_handler(
        self,
        source_id: str,
        command_name: str,
        handler_coroutine: t.Coroutine[t.Any, t.Any, None],
    ) -> None:
        future = asyncio.run_coroutine_threadsafe(handler_coroutine, self.loop)

        def _on_done(f: t.Any) -> None:
            try:
                f.result()
            except Exception as e:
                logger.error(f"Failed to process {command_name} for device {source_id}: {e}", exc_info=True)

        future.add_done_callback(_on_done)

    def handle_cmd_fcu_from_ac(
        self,
        source_id: str,
        message_id: str,
        target_id: list[JSONSerializable],
        payload: dict[str, JSONSerializable],
        timestamp: str,
    ) -> None:
        device = self.devices.get(source_id)

        if not device:
            logger.warning(f"Ignoring CMD_FCU_FROM_AC for unknown source_id {source_id}")
            return

        self._schedule_device_handler(source_id, "CMD_FCU_FROM_AC", device.handle_cmd_fcu_from_ac(payload))

    def handle_cmd_heartbeat(
        self,
        source_id: str,
        message_id: str,
        target_id: list[JSONSerializable],
        payload: dict[str, JSONSerializable],
        timestamp: str,
    ) -> None:
        device = self.devices.get(source_id)

        if not device:
            logger.warning(f"Ignoring CMD_HEARTBEAT for unknown source_id {source_id}")
            return

        self._schedule_device_handler(source_id, "CMD_HEARTBEAT", device.handle_cmd_heartbeat(payload))

    def handle_cmd_hdu_from_estia(
        self,
        source_id: str,
        message_id: str,
        target_id: list[JSONSerializable],
        payload: dict[str, JSONSerializable],
        timestamp: str,
    ) -> None:
        device = self.estia_devices.get(source_id)

        if not device:
            logger.warning(f"Ignoring CMD_HDU_FROM_ESTIA for unknown source_id {source_id}")
            return

        self._schedule_device_handler(source_id, "CMD_HDU_FROM_ESTIA", device.handle_cmd_hdu_from_estia(payload))

    def handle_cmd_heartbeat_estia(
        self,
        source_id: str,
        message_id: str,
        target_id: list[JSONSerializable],
        payload: dict[str, JSONSerializable],
        timestamp: str,
    ) -> None:
        device = self.estia_devices.get(source_id)

        if not device:
            logger.warning(f"Ignoring CMD_HEARTBEAT_ESTIA for unknown source_id {source_id}")
            return

        self._schedule_device_handler(source_id, "CMD_HEARTBEAT_ESTIA", device.handle_cmd_heartbeat_estia(payload))

    async def _handle_access_token_updated(self, access_token: str, access_token_type: str, consumer_id: str) -> None:
        self.access_token = access_token
        self.access_token_type = access_token_type
        self.consumer_id = consumer_id
        await self.on_access_token_updated_callback((access_token, access_token_type, consumer_id))

    @property
    def on_sas_token_updated_callback(self) -> ToshibaAcSasTokenUpdatedCallback:
        return self._on_sas_token_updated_callback

    @property
    def on_access_token_updated_callback(self) -> ToshibaAcAccessTokenUpdatedCallback:
        return self._on_access_token_updated_callback
