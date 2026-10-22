"""Talk to an LG ThinQ air conditioner through LG's official ThinQ Connect API."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from thinqconnect import ThinQApi, ThinQAPIException
from thinqconnect.devices.air_conditioner import AirConditionerDevice
from thinqconnect.devices.const import Property as P

AIR_CONDITIONER = "DEVICE_AIR_CONDITIONER"
POWER_ON = "POWER_ON"
POWER_OFF = "POWER_OFF"

# Modes whose target temperature lives in its own property on some models.
_MODE_TARGET_PREFIX = {"COOL": "COOL", "HEAT": "HEAT", "AUTO": "AUTO"}

# Timers count down from now. "start"/"stop" turn the unit on/off; "sleep" turns it off.
TIMER_KINDS = ("start", "stop", "sleep")
MAX_TIMER_MINUTES = 24 * 60
# (status resource, "is set" key, hour key, minute key) for each timer kind
_TIMER_FIELDS = {
    "start": ("timer", "relativeStartTimer", "relativeHourToStart", "relativeMinuteToStart"),
    "stop": ("timer", "relativeStopTimer", "relativeHourToStop", "relativeMinuteToStop"),
    "sleep": ("sleepTimer", "relativeStopTimer", "relativeHourToStop", "relativeMinuteToStop"),
}
_TIMER_HOUR_PROP = {
    "start": P.RELATIVE_HOUR_TO_START,
    "stop": P.RELATIVE_HOUR_TO_STOP,
    "sleep": P.SLEEP_TIMER_RELATIVE_HOUR_TO_STOP,
}
_TIMER_MINUTE_PROP = {
    "start": P.RELATIVE_MINUTE_TO_START,
    "stop": P.RELATIVE_MINUTE_TO_STOP,
    "sleep": P.SLEEP_TIMER_RELATIVE_MINUTE_TO_STOP,
}

_LOGGER = logging.getLogger(__name__)

# The unit got the command but didn't carry it out (2208), usually because it was still busy
# with the previous one, or answered too slowly (2209). Settings are absolute, so resending
# is safe; these are the waits before each retry.
TRANSIENT_CODES = {"2208", "2209"}
RETRY_DELAYS_S = (1.5, 3.0)


def _merge(payloads: list[dict]) -> dict:
    """Combine control payloads ({resource: {key: value}}) into one."""
    merged: dict = {}
    for payload in payloads:
        for resource, values in payload.items():
            merged.setdefault(resource, {}).update(values)
    return merged


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    alias: str
    model_name: str


@dataclass(frozen=True)
class AcState:
    power: bool | None = None
    mode: str | None = None
    modes: list[str] = field(default_factory=list)
    current_temp: float | None = None
    target_temp: float | None = None
    temp_min: float | None = None
    temp_max: float | None = None
    temp_step: float = 1
    can_set_temp: bool = False
    temp_modes: tuple[str, ...] = ()  # modes that have an adjustable target temperature
    unit: str = "C"
    fan_speed: str | None = None
    fan_speeds: list[str] = field(default_factory=list)
    # Extras. `None` means the unit doesn't report it; the can_* flags say whether it can be changed.
    swing: bool | None = None
    can_swing: bool = False
    power_save: bool | None = None
    can_power_save: bool = False
    display_light: bool | None = None
    can_display_light: bool = False
    filter_used_hours: int | None = None
    filter_lifetime_hours: int | None = None  # None when the unit doesn't report a lifetime
    error: bool = False
    # Minutes left on each timer; None when it isn't set.
    timer_start: int | None = None
    timer_stop: int | None = None
    timer_sleep: int | None = None
    timers: tuple[str, ...] = ()  # timer kinds this unit supports


class AcController:
    """Wraps one air conditioner. Every method must run on the same asyncio loop."""

    def __init__(self, api: ThinQApi):
        self._api = api
        self._device: AirConditionerDevice | None = None
        self._unit = "C"
        self._status: dict = {}  # last raw status, for fields the SDK doesn't expose
        # Commands go out one at a time, in the order they were issued.
        self._command_lock = asyncio.Lock()

    @property
    def device_id(self) -> str | None:
        return self._device.device_id if self._device else None

    async def list_air_conditioners(self) -> list[DeviceInfo]:
        devices = await self._api.async_get_device_list() or []
        return [
            DeviceInfo(d["deviceId"], d["deviceInfo"].get("alias", "Air conditioner"), d["deviceInfo"].get("modelName", ""))
            for d in devices
            if d.get("deviceInfo", {}).get("deviceType") == AIR_CONDITIONER
        ]

    async def connect(self, device: DeviceInfo, profile: dict | None = None) -> AcState:
        await self.load(device, profile)
        return await self.refresh()

    async def load(self, device: DeviceInfo, profile: dict | None = None) -> dict:
        """Set up the device from its profile (fetched if not given) without reading its status."""
        if profile is None:
            profile = await self._api.async_get_device_profile(device.device_id)
        self._device = AirConditionerDevice(
            thinq_api=self._api,
            device_id=device.device_id,
            device_type=AIR_CONDITIONER,
            model_name=device.model_name,
            alias=device.alias,
            reportable=True,
            profile=profile,
        )
        self._unit = "C" if self._readable(P.TARGET_TEMPERATURE_C) or self._readable(P.CURRENT_TEMPERATURE_C) else "F"
        return profile

    async def refresh(self) -> AcState:
        device = self._require_device()
        status = await self._api.async_get_device_status(device.device_id)
        device.set_status(status)
        self._status = status or {}
        return self.state()

    def apply_report(self, report: dict) -> AcState:
        """Merge a partial status pushed by LG (see live.py) and return the new state."""
        device = self._require_device()
        device.update_status(report)
        for resource, values in report.items():
            if isinstance(values, dict) and isinstance(self._status.get(resource), dict):
                self._status[resource] = {**self._status[resource], **values}
            else:
                self._status[resource] = values
        return self.state()

    def state(self) -> AcState:
        device = self._require_device()
        unit = self._unit
        write_prop = self._target_temperature_prop()
        limits = self._writable_values(write_prop) if write_prop else None
        power = device.get_status(P.AIR_CON_OPERATION_MODE)
        modes = list(self._writable_values(P.CURRENT_JOB_MODE) or [])
        return AcState(
            power=None if power is None else power == POWER_ON,
            mode=device.get_status(P.CURRENT_JOB_MODE),
            modes=modes,
            current_temp=device.get_status(getattr(P, f"CURRENT_TEMPERATURE_{unit}")),
            target_temp=self._read_target_temperature(),
            temp_min=limits.get("min") if isinstance(limits, dict) else None,
            temp_max=limits.get("max") if isinstance(limits, dict) else None,
            temp_step=limits.get("step", 1) if isinstance(limits, dict) else 1,
            can_set_temp=write_prop is not None,
            temp_modes=tuple(m for m in modes if self._target_temperature_prop(m) is not None),
            unit=unit,
            fan_speed=device.get_status(P.WIND_STRENGTH),
            fan_speeds=list(self._writable_values(P.WIND_STRENGTH) or []),
            swing=self._read(P.WIND_ROTATE_UP_DOWN),
            can_swing=self._writable(P.WIND_ROTATE_UP_DOWN),
            power_save=self._read(P.POWER_SAVE_ENABLED),
            can_power_save=self._writable(P.POWER_SAVE_ENABLED),
            display_light=None if (light := self._read(P.DISPLAY_LIGHT)) is None else light == "ON",
            can_display_light=self._writable(P.DISPLAY_LIGHT),
            filter_used_hours=self._status.get("filterInfo", {}).get("usedTime"),
            filter_lifetime_hours=self._status.get("filterInfo", {}).get("filterLifetime") or None,
            error=self._status.get("runState", {}).get("currentState") == "ERROR",
            timer_start=self._timer_minutes("start"),
            timer_stop=self._timer_minutes("stop"),
            timer_sleep=self._timer_minutes("sleep"),
            timers=tuple(kind for kind in TIMER_KINDS if self._writable(_TIMER_HOUR_PROP[kind])),
        )

    # Commands only send; call refresh() afterwards to read back what the unit reports.
    # The unit beeps once per command, so changes made together go out as one command.

    async def set_power(self, on: bool) -> None:
        await self.apply({"power": on})

    async def set_mode(self, mode: str) -> None:
        await self.apply({"mode": mode})

    async def set_fan_speed(self, speed: str) -> None:
        await self.apply({"fan_speed": speed})

    async def set_swing(self, on: bool) -> None:
        await self.apply({"swing": on})

    async def set_power_save(self, on: bool) -> None:
        await self.apply({"power_save": on})

    async def set_display_light(self, on: bool) -> None:
        await self.apply({"display_light": on})

    async def set_timer(self, kind: str, minutes: int) -> None:
        """Start a countdown timer of `minutes`; 0 cancels it."""
        if kind not in TIMER_KINDS:
            raise ValueError(f"Unknown timer {kind!r}.")
        await self.apply({f"timer_{kind}": minutes})

    async def cancel_timer(self, kind: str) -> None:
        await self.set_timer(kind, 0)

    async def set_target_temperature(self, value: float, mode: str | None = None) -> None:
        """Set the target for `mode` (default: the last mode read from the unit)."""
        await self.apply({"target_temp": value}, mode=mode)

    async def apply(self, changes: dict, mode: str | None = None) -> None:
        """Apply changes (AcState field -> value, e.g. from a preset) in one command.

        A target temperature belongs to the mode in `changes`, else `mode`, else the current one.
        Fields that aren't commands (like can_set_temp) are ignored.
        """
        from .presets import APPLY_ORDER

        mode = changes.get("mode") or mode
        parts = [self._payload(field, changes[field], mode) for field in APPLY_ORDER if field in changes]
        if not parts:
            return
        async with self._command_lock:
            if len(parts) == 1:
                await self._post(parts[0])
                return
            try:
                await self._post(_merge(parts))
            except ThinQAPIException as error:
                if str(error.code) in TRANSIENT_CODES:
                    raise  # already retried; splitting it up would only make it worse
                # Not every unit takes several settings at once (some refuse while off);
                # fall back to one command each, in a safe order.
                _LOGGER.info("Combined command refused (%s); sending one at a time", error)
                for part in parts:
                    await self._post(part)

    async def _post(self, payload: dict) -> None:
        device_id = self._require_device().device_id
        for delay in (*RETRY_DELAYS_S, None):
            try:
                await self._api.async_post_device_control(device_id=device_id, payload=payload)
                return
            except ThinQAPIException as error:
                if str(error.code) not in TRANSIENT_CODES or delay is None:
                    raise
                _LOGGER.info("Unit didn't take the command (%s); retrying in %gs", error.code, delay)
                await asyncio.sleep(delay)

    def _payload(self, field: str, value, mode: str | None) -> dict:
        """The control payload ({resource: {key: value}}) for one AcState field."""
        profiles = self._require_device().profiles
        if field == "power":
            return profiles.get_enum_attribute_payload(P.AIR_CON_OPERATION_MODE, POWER_ON if value else POWER_OFF)
        if field == "mode":
            return profiles.get_enum_attribute_payload(P.CURRENT_JOB_MODE, value)
        if field == "fan_speed":
            return profiles.get_enum_attribute_payload(P.WIND_STRENGTH, value)
        if field == "swing":
            return profiles.get_attribute_payload(P.WIND_ROTATE_UP_DOWN, value)
        if field == "power_save":
            return profiles.get_attribute_payload(P.POWER_SAVE_ENABLED, value)
        if field == "display_light":
            return profiles.get_enum_attribute_payload(P.DISPLAY_LIGHT, "ON" if value else "OFF")
        if field == "target_temp":
            prop = self._target_temperature_prop(mode)
            if prop is None:
                raise ValueError("This air conditioner doesn't accept a target temperature in the current mode.")
            if not profiles.check_range_attribute_writable(prop, value):
                limits = self._writable_values(prop) or {}
                raise ValueError(f"{value:g}° is outside the allowed range ({limits.get('min')}–{limits.get('max')}).")
            return _merge([profiles.get_attribute_payload(prop, value),
                           profiles.get_attribute_payload(P.TEMPERATURE_UNIT, self._unit)])
        if field.startswith("timer_"):
            return self._timer_payload(field.removeprefix("timer_"), value or 0)
        raise ValueError(f"Unknown setting {field!r}.")

    def _timer_payload(self, kind: str, minutes: int) -> dict:
        resource, flag, _, _ = _TIMER_FIELDS[kind]
        if minutes == 0:
            # The SDK has no call for cancelling, so use the documented payload directly.
            return {resource: {flag: "UNSET"}}
        if not 1 <= minutes <= MAX_TIMER_MINUTES:
            raise ValueError("Timers can be set from 1 minute to 24 hours.")
        hours, mins = divmod(int(minutes), 60)
        profiles = self._require_device().profiles
        parts = [profiles.get_attribute_payload(_TIMER_HOUR_PROP[kind], hours)]
        if mins or kind != "stop":  # like the SDK: the stop timer leaves out zero minutes
            parts.append(profiles.get_attribute_payload(_TIMER_MINUTE_PROP[kind], mins))
        return _merge(parts)

    def _require_device(self) -> AirConditionerDevice:
        if self._device is None:
            raise RuntimeError("No air conditioner connected yet.")
        return self._device

    def _target_temperature_candidates(self, mode: str | None = None) -> list[str]:
        """Target-temperature properties for a mode (default: current) and unit, most specific first."""
        mode = mode or self._require_device().get_status(P.CURRENT_JOB_MODE)
        candidates = [getattr(P, f"TARGET_TEMPERATURE_{self._unit}")]
        prefix = _MODE_TARGET_PREFIX.get(mode or "")
        if prefix:
            candidates.insert(0, getattr(P, f"{prefix}_TARGET_TEMPERATURE_{self._unit}"))
        return candidates

    def _target_temperature_prop(self, mode: str | None = None) -> str | None:
        """The property to write a new target temperature to."""
        return next((p for p in self._target_temperature_candidates(mode) if self._writable(p)), None)

    def _read_target_temperature(self) -> float | None:
        # Some units (e.g. S3NQ09AM31A) take the setpoint through a write-only property such as
        # coolTargetTemperature and only report it back through the generic targetTemperature.
        device = self._require_device()
        for prop in self._target_temperature_candidates():
            if self._readable(prop) and (value := device.get_status(prop)) is not None:
                return value
        return None

    def _read(self, prop: str):
        return self._require_device().get_status(prop) if self._readable(prop) else None

    def _timer_minutes(self, kind: str) -> int | None:
        resource, flag, hour_key, minute_key = _TIMER_FIELDS[kind]
        timer = self._status.get(resource) or {}
        if timer.get(flag) != "SET":
            return None
        return int(timer.get(hour_key) or 0) * 60 + int(timer.get(minute_key) or 0)

    def _readable(self, prop: str) -> bool:
        try:
            return bool(self._require_device().profiles.check_attribute_readable(prop))
        except AttributeError:  # the profile never mentioned this property
            return False

    def _writable(self, prop: str) -> bool:
        try:
            return bool(self._require_device().profiles.check_attribute_writable(prop))
        except AttributeError:
            return False

    def _writable_values(self, prop: str):
        if not self._writable(prop):
            return None
        return self._require_device().profiles.get_property(prop).get("w")
