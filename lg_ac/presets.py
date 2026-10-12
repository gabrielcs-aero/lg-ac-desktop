"""One-click presets. Each is a set of target settings, planned against what the unit supports."""

from __future__ import annotations

from dataclasses import dataclass

from .controller import AcState


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    icon: str
    description: str
    # AcState field -> wanted value. Fan speeds list preferences in order;
    # "min" as a temperature means the coldest the unit allows.
    settings: dict


PRESETS = (
    Preset(
        "sleep", "Sleep", "preset-sleep", "25°, quiet fan, display off, turns off after 8 h",
        {"power": True, "mode": "COOL", "target_temp": 25, "fan_speed": ("LOW", "LOW_MID", "MID"),
         "swing": False, "display_light": False, "timer_sleep": 8 * 60},
    ),
    Preset(
        "max_cool", "Max cool", "preset-boost", "Coldest setting, full fan, swing on",
        {"power": True, "mode": "COOL", "target_temp": "min", "fan_speed": ("HIGH", "MID_HIGH"),
         "swing": True, "power_save": False},
    ),
    Preset(
        "eco", "Eco", "preset-eco", "26°, low fan, power save on",
        {"power": True, "mode": "COOL", "target_temp": 26, "fan_speed": ("LOW", "LOW_MID", "MID"),
         "power_save": True},
    ),
)

_BY_ID = {preset.id: preset for preset in PRESETS}

# The order changes are applied in: the unit must be on before anything else, and the
# temperature belongs to a mode, so the mode goes first.
APPLY_ORDER = ("power", "mode", "target_temp", "fan_speed", "swing", "power_save", "display_light",
               "timer_start", "timer_stop", "timer_sleep")


def get_preset(preset_id: str) -> Preset:
    try:
        return _BY_ID[preset_id]
    except KeyError:
        raise ValueError(f"Unknown preset {preset_id!r}. Choose from: {', '.join(_BY_ID)}.") from None


def plan(preset: Preset, state: AcState) -> dict:
    """The changes (AcState field -> value) this preset needs, leaving out unsupported ones."""
    wanted = preset.settings
    changes: dict = {}

    if wanted.get("power") and state.power is not None and not state.power:
        changes["power"] = True
    mode = wanted.get("mode") if wanted.get("mode") in state.modes else state.mode
    if mode != state.mode:
        changes["mode"] = mode

    target = wanted.get("target_temp")
    if target == "min":
        target = state.temp_min
    if target is not None and mode in state.temp_modes and state.temp_min is not None:
        target = min(max(target, state.temp_min), state.temp_max)
        if target != state.target_temp:
            changes["target_temp"] = target

    fan = next((speed for speed in wanted.get("fan_speed", ()) if speed in state.fan_speeds), None)
    if fan and fan != state.fan_speed:
        changes["fan_speed"] = fan

    for field, can in (("swing", state.can_swing), ("power_save", state.can_power_save),
                       ("display_light", state.can_display_light)):
        if field in wanted and can and wanted[field] != getattr(state, field):
            changes[field] = wanted[field]

    for kind in ("start", "stop", "sleep"):
        field = f"timer_{kind}"
        if field in wanted and kind in state.timers and wanted[field] != getattr(state, field):
            changes[field] = wanted[field]

    if "mode" in changes:  # keep the optimistic display consistent with the new mode
        changes["can_set_temp"] = mode in state.temp_modes
    return changes


def presets_for(state: AcState) -> list[dict]:
    """Presets as plain data (for the UI and the CLI), with the changes each would make now."""
    return [
        {"id": p.id, "name": p.name, "icon": p.icon, "description": p.description, "changes": plan(p, state)}
        for p in PRESETS
    ]
