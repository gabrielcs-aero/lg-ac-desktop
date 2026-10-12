"""Talk to an LG ThinQ air conditioner through LG's official ThinQ Connect API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from thinqconnect import ThinQApi
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

    async def set_power(self, on: bool) -> None:
        async with self._command_lock:
            await self._require_device().set_air_con_operation_mode(POWER_ON if on else POWER_OFF)

    async def set_mode(self, mode: str) -> None:
        async with self._command_lock:
            await self._require_device().set_current_job_mode(mode)

    async def set_fan_speed(self, speed: str) -> None:
        async with self._command_lock:
            await self._require_device().set_wind_strength(speed)

    async def set_swing(self, on: bool) -> None:
        async with self._command_lock:
            await self._require_device().set_wind_rotate_up_down(on)

    async def set_power_save(self, on: bool) -> None:
        async with self._command_lock:
            await self._require_device().set_power_save_enabled(on)

    async def set_display_light(self, on: bool) -> None:
        async with self._command_lock:
            await self._require_device().set_display_light("ON" if on else "OFF")

    async def set_timer(self, kind: str, minutes: int) -> None:
        """Start a countdown timer of `minutes`; 0 cancels it."""
        if kind not in TIMER_KINDS:
            raise ValueError(f"Unknown timer {kind!r}.")
        if minutes == 0:
            return await self.cancel_timer(kind)
        if not 1 <= minutes <= MAX_TIMER_MINUTES:
            raise ValueError("Timers can be set from 1 minute to 24 hours.")
        device = self._require_device()
        hours, mins = divmod(int(minutes), 60)
        setter = {
            "start": device.set_relative_time_to_start,
            "stop": device.set_relative_time_to_stop,
            "sleep": device.set_sleep_timer_relative_time_to_stop,
        }[kind]
        async with self._command_lock:
            await setter(hours, mins)

    async def cancel_timer(self, kind: str) -> None:
        # The SDK has no call for this, so send the documented payload directly.
        resource, flag, _, _ = _TIMER_FIELDS[kind]
        device = self._require_device()
        async with self._command_lock:
            await self._api.async_post_device_control(device_id=device.device_id, payload={resource: {flag: "UNSET"}})

    async def apply(self, changes: dict) -> None:
        """Apply several changes (AcState field -> value) in a safe order, e.g. from a preset."""
        from .presets import APPLY_ORDER

        mode = changes.get("mode")
        steps = {
            "power": self.set_power,
            "mode": self.set_mode,
            "target_temp": lambda value: self.set_target_temperature(value, mode=mode),
            "fan_speed": self.set_fan_speed,
            "swing": self.set_swing,
            "power_save": self.set_power_save,
            "display_light": self.set_display_light,
            "timer_start": lambda minutes: self.set_timer("start", minutes or 0),
            "timer_stop": lambda minutes: self.set_timer("stop", minutes or 0),
            "timer_sleep": lambda minutes: self.set_timer("sleep", minutes or 0),
        }
        for field in APPLY_ORDER:
            if field in changes:
                await steps[field](changes[field])

    async def set_target_temperature(self, value: float, mode: str | None = None) -> None:
        """Set the target for `mode` (default: the last mode read from the unit)."""
        device = self._require_device()
        prop = self._target_temperature_prop(mode)
        if prop is None:
            raise ValueError("This air conditioner doesn't accept a target temperature in the current mode.")
        if not device.profiles.check_range_attribute_writable(prop, value):
            limits = self._writable_values(prop) or {}
            raise ValueError(f"{value:g}° is outside the allowed range ({limits.get('min')}–{limits.get('max')}).")
        async with self._command_lock:
            await device.do_multi_attribute_command({prop: value, P.TEMPERATURE_UNIT: self._unit})

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
