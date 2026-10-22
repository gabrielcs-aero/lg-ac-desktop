"""A simulated LG wall-mounted air conditioner, used by --demo mode and the tests.

Responses follow the ThinQ Connect API format; the values are made up.
"""

import asyncio
import copy
import json
import time
from pathlib import Path

from thinqconnect import ThinQAPIException

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


def _number():
    return {"type": "number", "mode": ["r", "w"]}


def _cancel_only():
    return {"type": "enum", "mode": ["r", "w"], "value": {"r": ["SET", "UNSET"], "w": ["UNSET"]}}


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
        "timer": {
            "relativeHourToStop": _number(),
            "relativeMinuteToStop": _number(),
            "relativeStopTimer": _cancel_only(),
            "relativeHourToStart": _number(),
            "relativeMinuteToStart": _number(),
            "relativeStartTimer": _cancel_only(),
        },
        "sleepTimer": {
            "relativeHourToStop": _number(),
            "relativeMinuteToStop": _number(),
            "relativeStopTimer": _cancel_only(),
        },
        "powerSave": {"powerSaveEnabled": _enum([False, True])},
        "windDirection": {"rotateUpDown": _enum([True, False])},
        "display": {"light": _enum(["ON", "OFF"])},
        "filterInfo": {"filterLifetime": {"type": "number", "mode": ["r"]}, "usedTime": {"type": "number", "mode": ["r"]}},
        "runState": {"currentState": _enum(["NORMAL", "ERROR"], mode=("r",))},
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
    "timer": {"relativeStartTimer": "UNSET", "relativeStopTimer": "UNSET"},
    "sleepTimer": {"relativeStopTimer": "UNSET"},
    "powerSave": {"powerSaveEnabled": False},
    "windDirection": {"rotateUpDown": False},
    "display": {"light": "ON"},
    "filterInfo": {"filterLifetime": 0, "usedTime": 720},
    "runState": {"currentState": "NORMAL"},
}

# Which "is set" flag each timer field belongs to.
_TIMER_FLAGS = {
    "relativeHourToStart": "relativeStartTimer",
    "relativeMinuteToStart": "relativeStartTimer",
    "relativeHourToStop": "relativeStopTimer",
    "relativeMinuteToStop": "relativeStopTimer",
}


# The simulated room: with the unit off it drifts toward ROOM_C; when cooling it moves 0.5°
# toward the setpoint every few seconds (faster with a strong fan).
ROOM_C = 30.0
_SECONDS_PER_STEP = {"HIGH": 6, "MID_HIGH": 9, "MID": 12}
_SECONDS_PER_STEP_DEFAULT = 18
_SECONDS_PER_STEP_WARMING = 45


class DemoApi:
    """Stands in for ThinQApi: serves the sample data and applies commands to it."""

    def __init__(self, latency: float = 0, simulate_room: bool = False, clock=time.time):
        self.status = copy.deepcopy(STATUS)
        self.commands = []
        self.latency = latency  # seconds each request takes, like a trip to LG's cloud
        self.delays: list[float] = []  # per-command overrides, consumed in order
        self.refuse_combined = False  # reject commands that change more than one resource
        self.failures: list[str] = []  # error codes to raise on the next commands, in order
        self.simulate_room = simulate_room
        self.clock = clock
        self.sim_time: float | None = None  # when the room temperature was last moved

    def _simulate(self) -> None:
        now = self.clock()
        if self.sim_time is None:
            self.sim_time = now
            return
        celsius = next(e for e in self.status["temperatureInUnits"] if e["unit"] == "C")
        current = celsius["currentTemperature"]
        cooling = (self.status["operation"]["airConOperationMode"] == "POWER_ON"
                   and self.status["airConJobMode"]["currentJobMode"] in ("COOL", "AUTO"))
        goal = celsius["targetTemperature"] if cooling else ROOM_C
        if goal < current:
            period = _SECONDS_PER_STEP.get(self.status["airFlow"].get("windStrength"), _SECONDS_PER_STEP_DEFAULT)
        else:
            period = _SECONDS_PER_STEP_WARMING
        steps = int((now - self.sim_time) // period)
        if steps == 0:
            return
        self.sim_time += steps * period
        current = max(goal, current - 0.5 * steps) if goal < current else min(goal, current + 0.5 * steps)
        celsius["currentTemperature"] = current
        for entry in self.status["temperatureInUnits"]:
            if entry["unit"] == "F":
                entry["currentTemperature"] = round(current * 9 / 5 + 32)

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
        if self.simulate_room:
            self._simulate()
        return copy.deepcopy(self.status)

    async def async_post_device_control(self, device_id, payload):
        await self._wait(self.delays.pop(0) if self.delays else None)
        payload = {resource: dict(values) for resource, values in payload.items()}
        if self.failures:
            raise ThinQAPIException(self.failures.pop(0), "Fail device control", {})
        if self.refuse_combined and len(payload) > 1:
            raise ThinQAPIException("1100", "Bad request", {})
        self.commands.append(payload)
        for resource, values in payload.items():
            if resource == "temperatureInUnits":
                for entry in self.status[resource]:
                    if entry["unit"] == values.get("unit"):
                        for key, value in values.items():
                            if key.endswith("argetTemperature"):  # cool/auto setpoints read back as the target
                                entry["targetTemperature"] = value
            elif resource in ("timer", "sleepTimer"):
                self._apply_timer(self.status.setdefault(resource, {}), values)
            else:
                self.status.setdefault(resource, {}).update(values)
        return {}

    @staticmethod
    def _apply_timer(timer: dict, values: dict) -> None:
        for key, value in values.items():
            if value == "UNSET":  # cancel: drop the countdown that belongs to this flag
                timer[key] = "UNSET"
                for field, flag in _TIMER_FLAGS.items():
                    if flag == key:
                        timer.pop(field, None)
            elif key in _TIMER_FLAGS:
                timer[key] = value
                timer[_TIMER_FLAGS[key]] = "SET"
                # Setting only the hours clears any leftover minutes, like the real unit.
                if key.startswith("relativeHour"):
                    timer.setdefault(key.replace("Hour", "Minute"), 0)


class FileDemoApi(DemoApi):
    """A DemoApi kept in a file, so the app, the command line and the panel widgets all share
    one simulated unit, with a room that warms up and cools down."""

    def __init__(self, path: Path, latency: float = 0, clock=time.time):
        super().__init__(latency=latency, simulate_room=True, clock=clock)
        self.path = path

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return
        if isinstance(data, dict) and isinstance(data.get("status"), dict):
            self.status, self.sim_time = data["status"], data.get("sim_time")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"status": self.status, "sim_time": self.sim_time}))
        tmp.replace(self.path)

    async def async_get_device_status(self, device_id):
        self._load()
        before = (copy.deepcopy(self.status), self.sim_time)
        result = await super().async_get_device_status(device_id)
        if (self.status, self.sim_time) != before:  # only write (and wake watchers) on a change
            self._save()
        return result

    async def async_post_device_control(self, device_id, payload):
        await self._wait(self.delays.pop(0) if self.delays else None)
        self._load()  # another process may have changed the unit while we waited
        latency, self.latency = self.latency, 0
        try:
            result = await super().async_post_device_control(device_id, payload)
        finally:
            self.latency = latency
        self._save()
        return result
