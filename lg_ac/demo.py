"""A simulated LG wall-mounted air conditioner, used by --demo mode and the tests.

Responses follow the ThinQ Connect API format; the values are made up.
"""

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


def _range(lo, hi, step=1):
    rng = {"min": lo, "max": hi, "step": step}
    return {"type": "range", "mode": ["r", "w"], "value": {"r": rng, "w": rng}}


PROFILE = {
    "notification": {"push": []},
    "property": {
        "airConJobMode": {"currentJobMode": _enum(["COOL", "AIR_DRY", "FAN"])},
        "operation": {"airConOperationMode": _enum(["POWER_ON", "POWER_OFF"])},
        "temperatureInUnits": [
            {
                "unit": "C",
                "currentTemperature": {"type": "number", "mode": ["r"]},
                "targetTemperature": _range(18, 30),
            },
            {
                "unit": "F",
                "currentTemperature": {"type": "number", "mode": ["r"]},
                "targetTemperature": _range(64, 86),
            },
        ],
        "airFlow": {"windStrength": _enum(["LOW", "MID", "HIGH", "AUTO"])},
    },
}

STATUS = {
    "airConJobMode": {"currentJobMode": "COOL"},
    "operation": {"airConOperationMode": "POWER_ON"},
    "temperatureInUnits": [
        {"currentTemperature": 26, "targetTemperature": 22, "unit": "C"},
        {"currentTemperature": 79, "targetTemperature": 72, "unit": "F"},
    ],
    "airFlow": {"windStrength": "MID"},
}


class DemoApi:
    """Stands in for ThinQApi: serves the sample data and applies commands to it."""

    def __init__(self):
        self.status = copy.deepcopy(STATUS)
        self.commands = []

    async def async_get_device_list(self):
        return copy.deepcopy(DEVICE_LIST)

    async def async_get_device_profile(self, device_id):
        return copy.deepcopy(PROFILE)

    async def async_get_device_status(self, device_id):
        return copy.deepcopy(self.status)

    async def async_post_device_control(self, device_id, payload):
        payload = {resource: dict(values) for resource, values in payload.items()}
        self.commands.append(payload)
        for resource, values in payload.items():
            if resource == "temperatureInUnits":
                for entry in self.status[resource]:
                    if entry["unit"] == values.get("unit"):
                        entry.update({k: v for k, v in values.items() if k != "unit"})
            else:
                self.status.setdefault(resource, {}).update(values)
        return {}
