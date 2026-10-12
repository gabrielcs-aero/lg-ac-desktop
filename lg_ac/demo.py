"""A simulated LG wall-mounted air conditioner, used by --demo mode and the tests.

Responses follow the ThinQ Connect API format; the values are made up.
"""

import asyncio
import copy

DEVICE_LIST = [
    {
        "deviceId": "test-device-id",
        "deviceInfo": {
            "deviceType": "DEVICE_AIR_CONDITIONER",
            "modelName": "S3NQ09AM31A",
            "alias": "Air conditioner",
            "reportable": True,
        },
    },
    {
        "deviceId": "fridge-id",
        "deviceInfo": {
            "deviceType": "DEVICE_REFRIGERATOR",
            "modelName": "FRIDGE",
            "alias": "Fridge",
            "reportable": True,
        },
    },
]


def _enum(values, mode=("r", "w")):
    return {"type": "enum", "mode": list(mode), "value": {m: list(values) for m in mode}}


def _write_only_range(lo, hi, step):
    return {"type": "range", "mode": ["w"], "value": {"w": {"min": lo, "max": hi, "step": step}}}


def _temperatures(unit, lo, hi, step):
    # Mirrors the S3NQ09AM31A: the setpoint is written per mode (cool/auto) but only read back
    # through the generic, read-only targetTemperature.
    return {
        "unit": unit,
        "currentTemperature": {"type": "number", "mode": ["r"]},
        "targetTemperature": {"type": "number", "mode": ["r"]},
        "minTargetTemperature": {"type": "number", "mode": ["r"]},
        "maxTargetTemperature": {"type": "number", "mode": ["r"]},
        "coolTargetTemperature": _write_only_range(lo, hi, step),
        "autoTargetTemperature": _write_only_range(lo, hi, step),
    }


PROFILE = {
    "notification": {"push": []},
    "property": {
        "airConJobMode": {"currentJobMode": _enum(["COOL", "AIR_DRY", "FAN", "AUTO"])},
        "operation": {"airConOperationMode": _enum(["POWER_ON", "POWER_OFF"])},
        "temperatureInUnits": [_temperatures("C", 18, 30, 0.5), _temperatures("F", 64, 86, 1)],
        "airFlow": {"windStrength": _enum(["NATURE", "LOW", "LOW_MID", "MID", "MID_HIGH", "HIGH"])},
    },
}

STATUS = {
    "airConJobMode": {"currentJobMode": "COOL"},
    "operation": {"airConOperationMode": "POWER_ON"},
    "temperatureInUnits": [
        {"currentTemperature": 26, "targetTemperature": 22, "minTargetTemperature": 18, "maxTargetTemperature": 30, "unit": "C"},
        {"currentTemperature": 79, "targetTemperature": 72, "minTargetTemperature": 64, "maxTargetTemperature": 86, "unit": "F"},
    ],
    "airFlow": {"windStrength": "MID"},
}


class DemoApi:
    """Stands in for ThinQApi: serves the sample data and applies commands to it."""

    def __init__(self, latency: float = 0):
        self.status = copy.deepcopy(STATUS)
        self.commands = []
        self.latency = latency  # seconds each request takes, like a trip to LG's cloud
        self.delays: list[float] = []  # per-command overrides, consumed in order

    async def _wait(self, delay=None):
        delay = self.latency if delay is None else delay
        if delay:
            await asyncio.sleep(delay)

    async def async_get_device_list(self):
        return copy.deepcopy(DEVICE_LIST)

    async def async_get_device_profile(self, device_id):
        return copy.deepcopy(PROFILE)

    async def async_get_device_status(self, device_id):
        await self._wait()
        return copy.deepcopy(self.status)

    async def async_post_device_control(self, device_id, payload):
        await self._wait(self.delays.pop(0) if self.delays else None)
        payload = {resource: dict(values) for resource, values in payload.items()}
        self.commands.append(payload)
        for resource, values in payload.items():
            if resource == "temperatureInUnits":
                for entry in self.status[resource]:
                    if entry["unit"] == values.get("unit"):
                        for key, value in values.items():
                            if key.endswith("argetTemperature"):  # cool/auto setpoints read back as the target
                                entry["targetTemperature"] = value
            else:
                self.status.setdefault(resource, {}).update(values)
        return {}
