"""Talk to an LG ThinQ air conditioner through LG's official ThinQ Connect API."""

from __future__ import annotations

from dataclasses import dataclass, field

from thinqconnect import ThinQApi
from thinqconnect.devices.air_conditioner import AirConditionerDevice
from thinqconnect.devices.const import Property as P

AIR_CONDITIONER = "DEVICE_AIR_CONDITIONER"
POWER_ON = "POWER_ON"
POWER_OFF = "POWER_OFF"

# Modes whose target temperature lives in its own property on some models.
_MODE_TARGET_PREFIX = {"COOL": "COOL", "HEAT": "HEAT", "AUTO": "AUTO"}


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
    unit: str = "C"
    fan_speed: str | None = None
    fan_speeds: list[str] = field(default_factory=list)


class AcController:
    """Wraps one air conditioner. Every method must run on the same asyncio loop."""

    def __init__(self, api: ThinQApi):
        self._api = api
        self._device: AirConditionerDevice | None = None
        self._unit = "C"

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

    async def connect(self, device: DeviceInfo) -> AcState:
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
        return await self.refresh()

    async def refresh(self) -> AcState:
        device = self._require_device()
        status = await self._api.async_get_device_status(device.device_id)
        device.set_status(status)
        return self.state()

    def state(self) -> AcState:
        device = self._require_device()
        unit = self._unit
        write_prop = self._target_temperature_prop()
        limits = self._writable_values(write_prop) if write_prop else None
        power = device.get_status(P.AIR_CON_OPERATION_MODE)
        return AcState(
            power=None if power is None else power == POWER_ON,
            mode=device.get_status(P.CURRENT_JOB_MODE),
            modes=list(self._writable_values(P.CURRENT_JOB_MODE) or []),
            current_temp=device.get_status(getattr(P, f"CURRENT_TEMPERATURE_{unit}")),
            target_temp=self._read_target_temperature(),
            temp_min=limits.get("min") if isinstance(limits, dict) else None,
            temp_max=limits.get("max") if isinstance(limits, dict) else None,
            temp_step=limits.get("step", 1) if isinstance(limits, dict) else 1,
            can_set_temp=write_prop is not None,
            unit=unit,
            fan_speed=device.get_status(P.WIND_STRENGTH),
            fan_speeds=list(self._writable_values(P.WIND_STRENGTH) or []),
        )

    async def set_power(self, on: bool) -> AcState:
        await self._require_device().set_air_con_operation_mode(POWER_ON if on else POWER_OFF)
        return await self.refresh()

    async def set_mode(self, mode: str) -> AcState:
        await self._require_device().set_current_job_mode(mode)
        return await self.refresh()

    async def set_fan_speed(self, speed: str) -> AcState:
        await self._require_device().set_wind_strength(speed)
        return await self.refresh()

    async def set_target_temperature(self, value: float) -> AcState:
        device = self._require_device()
        prop = self._target_temperature_prop()
        if prop is None:
            raise ValueError("This air conditioner doesn't accept a target temperature in the current mode.")
        if not device.profiles.check_range_attribute_writable(prop, value):
            limits = self._writable_values(prop) or {}
            raise ValueError(f"{value:g}° is outside the allowed range ({limits.get('min')}–{limits.get('max')}).")
        await device.do_multi_attribute_command({prop: value, P.TEMPERATURE_UNIT: self._unit})
        return await self.refresh()

    def _require_device(self) -> AirConditionerDevice:
        if self._device is None:
            raise RuntimeError("No air conditioner connected yet.")
        return self._device

    def _target_temperature_candidates(self) -> list[str]:
        """Target-temperature properties for the current mode and unit, most specific first."""
        candidates = [getattr(P, f"TARGET_TEMPERATURE_{self._unit}")]
        prefix = _MODE_TARGET_PREFIX.get(self._require_device().get_status(P.CURRENT_JOB_MODE) or "")
        if prefix:
            candidates.insert(0, getattr(P, f"{prefix}_TARGET_TEMPERATURE_{self._unit}"))
        return candidates

    def _target_temperature_prop(self) -> str | None:
        """The property to write a new target temperature to."""
        return next((p for p in self._target_temperature_candidates() if self._writable(p)), None)

    def _read_target_temperature(self) -> float | None:
        # Some units (e.g. S3NQ09AM31A) take the setpoint through a write-only property such as
        # coolTargetTemperature and only report it back through the generic targetTemperature.
        device = self._require_device()
        for prop in self._target_temperature_candidates():
            if self._readable(prop) and (value := device.get_status(prop)) is not None:
                return value
        return None

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
