import unittest
from unittest.mock import AsyncMock, patch

from custom_components.toshiba_ac_community.toshiba_ac.device_manager import (  # noqa: F401
    ToshibaAcDeviceManager,
)
from custom_components.toshiba_ac_community.toshiba_ac.utils.http_api import (
    ToshibaAcHttpApi,
)


class EnergyConsumptionTest(unittest.IsolatedAsyncioTestCase):
    async def test_device_without_metering_does_not_hide_later_results(self) -> None:
        api = ToshibaAcHttpApi("user", "password", "0123456789abcdef")
        with patch.object(
            api,
            "request_api",
            AsyncMock(
                return_value=[
                    {
                        "ACDeviceUniqueId": "no-metering",
                        "EnergyConsumption": None,
                    },
                    {
                        "ACDeviceUniqueId": "living-room",
                        "EnergyConsumption": [
                            {"Energy": 237021, "Time": "1"},
                            {"Energy": 211538, "Time": "2"},
                        ],
                    },
                ]
            ),
        ):
            result = await api.get_devices_energy_consumption(["no-metering", "living-room"])

        self.assertNotIn("no-metering", result)
        self.assertEqual(result["living-room"].energy_wh, 448559)


if __name__ == "__main__":
    unittest.main()
