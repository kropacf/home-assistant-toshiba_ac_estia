# Copyright 2022 Kamil Sroka

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntFlag, auto
import typing as t


@dataclass
class ToshibaAcDeviceEnergyConsumption:
    energy_wh: float
    since: datetime


class ToshibaAcStatus(Enum):
    ON = auto()
    OFF = auto()
    NONE = None


class ToshibaAcMode(Enum):
    AUTO = auto()
    COOL = auto()
    HEAT = auto()
    DRY = auto()
    FAN = auto()
    NONE = None


class ToshibaAcFanMode(Enum):
    AUTO = auto()
    QUIET = auto()
    LOW = auto()
    MEDIUM_LOW = auto()
    MEDIUM = auto()
    MEDIUM_HIGH = auto()
    HIGH = auto()
    NONE = None


class ToshibaAcSwingMode(Enum):
    OFF = auto()
    SWING_VERTICAL = auto()
    SWING_HORIZONTAL = auto()
    SWING_VERTICAL_AND_HORIZONTAL = auto()
    FIXED_1 = auto()
    FIXED_2 = auto()
    FIXED_3 = auto()
    FIXED_4 = auto()
    FIXED_5 = auto()
    HADA = auto()
    NONE = None


class ToshibaAcVerticalSwingMode(Enum):
    FIXED_1 = auto()
    FIXED_2 = auto()
    FIXED_3 = auto()
    FIXED_4 = auto()
    FIXED_5 = auto()
    SWING = auto()
    NONE = None


class ToshibaAcHorizontalSwingMode(Enum):
    FIXED_1 = auto()
    FIXED_2 = auto()
    FIXED_3 = auto()
    FIXED_4 = auto()
    FIXED_5 = auto()
    SWING = auto()
    NONE = None


class ToshibaAcPowerSelection(Enum):
    POWER_50 = auto()
    POWER_75 = auto()
    POWER_100 = auto()
    NONE = None


class ToshibaAcMeritB(Enum):
    FIREPLACE_1 = auto()
    FIREPLACE_2 = auto()
    OFF = auto()
    NONE = None


class ToshibaAcMeritA(Enum):
    HIGH_POWER = auto()
    CDU_SILENT_1 = auto()
    ECO = auto()
    HEATING_8C = auto()
    SLEEP_CARE = auto()
    FLOOR = auto()
    COMFORT = auto()
    CDU_SILENT_2 = auto()
    OFF = auto()
    NONE = None


class ToshibaAcAirPureIon(Enum):
    OFF = auto()
    ON = auto()
    NONE = None


class ToshibaAcSelfCleaning(Enum):
    ON = auto()
    OFF = auto()
    NONE = None


class ToshibaAcWirelessLed(Enum):
    ON = auto()
    OFF = auto()
    NONE = None


class EstiaCompressorStatus(Enum):
    OFF = auto()
    DHW = auto()
    HEAT = auto()
    NONE = None


class EstiaWaterMode(Enum):
    AUTO = auto()
    COOL = auto()
    HEAT = auto()
    NONE = None


@dataclass
class EstiaWaterTemperatures:
    """Raw readings of the Estia water circuit sensors.

    Raw because the same six values arrive from three different places - the
    status string, the AMQP heartbeat and the extended info endpoint - and
    decoding them belongs in one place, ToshibaEstiaFcuState.WaterTemperature.
    """

    room_water: t.Optional[int] = None
    twi: t.Optional[int] = None
    two: t.Optional[int] = None
    tho: t.Optional[int] = None
    to: t.Optional[int] = None
    tfi: t.Optional[int] = None


class EstiaFunctionFlag(IntFlag):
    """Bits of the Estia function register, command byte 10.

    The status string reports the same bits in byte 19, except AUTO, which is
    reported per zone in bytes 11 and 18. 0x04, 0x40 and 0x80 have never been
    observed and their meaning is unknown.
    """

    AUTO = 0x01
    FROST_PROTECTION = 0x02
    NIGHT_SETBACK = 0x08
    DHW_BOOST = 0x10
    ANTI_BACTERIAL = 0x20


@dataclass
class EstiaSetpointLimits:
    """Setpoint ranges from the device record, in degrees Celsius.

    None means the record did not carry the field.
    """

    dhw_min: t.Optional[float] = None
    dhw_max: t.Optional[float] = None
    zone1_heat_min: t.Optional[float] = None
    zone1_heat_max: t.Optional[float] = None
    zone2_heat_min: t.Optional[float] = None
    zone2_heat_max: t.Optional[float] = None
