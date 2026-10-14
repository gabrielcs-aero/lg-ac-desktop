"""Quick cool (or quick heat): reach a temperature as fast as possible, then hold it.

While boosting, the unit runs flat out: the coldest (or warmest) setpoint it allows, the
strongest fan, power save off. Once the room reaches the target, the setpoint becomes the
target and the fan goes back to what it was, so the unit's own thermostat holds it there.

The watching is done by a small background process (`lg-ac-cli boost-run`), so it keeps going
when the app or the panel popup is closed. Its state lives in a JSON file that the app, the
widgets and the command line all read. If that process dies (say, the PC restarts), the next
status read starts a new one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .controller import AcController, AcState

_LOGGER = logging.getLogger(__name__)

POLL_S = 60  # how often the background process checks the room temperature
NEAR_POLL_S = 20  # ...and when the room is within a degree of the target
MAX_DURATION_S = 3 * 3600  # give up boosting after this long and just hold the target
FAST_FANS = ("HIGH", "MID_HIGH", "MID")  # strongest first


@dataclass
class Boost:
    target: float
    mode: str  # "COOL" or "HEAT"
    setpoint: float  # what the unit is set to while boosting
    fan: str | None  # fan speed while boosting
    restore_fan: str | None  # fan speed to go back to afterwards
    restore_power_save: bool  # power save was on before, so turn it back on afterwards
    started: float
    run_id: str = ""
    pid: int = 0


def label(mode: str | None) -> str:
    return "Quick heat" if mode == "HEAT" else "Quick cool"


def boost_mode(state: AcState) -> str | None:
    """The mode a boost would use: heat if the unit is heating, otherwise cool."""
    if state.mode == "HEAT" and "HEAT" in state.temp_modes:
        return "HEAT"
    return "COOL" if "COOL" in state.temp_modes else None


def reached(mode: str, current: float, target: float) -> bool:
    return current >= target if mode == "HEAT" else current <= target


def plan_start(state: AcState, target: float, now: float) -> tuple[Boost | None, dict]:
    """The boost to run and the changes (AcState field -> value) that start it.

    If the room is already at the target there's nothing to boost: the boost is None and the
    changes just set the target.
    """
    mode = boost_mode(state)
    if mode is None:
        raise ValueError("This air conditioner can't cool or heat to a set temperature.")
    if state.current_temp is None:
        raise ValueError("This air conditioner doesn't report the room temperature.")
    check_target(state, target)

    changes: dict = {}
    if not state.power:
        changes["power"] = True
    if mode != state.mode:
        changes["mode"] = mode
        changes["can_set_temp"] = True
    if reached(mode, state.current_temp, target):
        if target != state.target_temp or "mode" in changes:
            changes["target_temp"] = target
        return None, changes

    setpoint = state.temp_min if mode == "COOL" else state.temp_max
    if setpoint != state.target_temp or "mode" in changes:
        changes["target_temp"] = setpoint
    fan = next((speed for speed in FAST_FANS if speed in state.fan_speeds), state.fan_speed)
    if fan != state.fan_speed:
        changes["fan_speed"] = fan
    restore_power_save = bool(state.can_power_save and state.power_save)
    if restore_power_save:
        changes["power_save"] = False
    return Boost(target, mode, setpoint, fan, state.fan_speed, restore_power_save, now), changes


def check_target(state: AcState, target: float) -> None:
    if state.temp_min is None or state.temp_max is None:
        raise ValueError("This air conditioner doesn't accept a target temperature.")
    if not state.temp_min <= target <= state.temp_max:
        raise ValueError(f"{target:g}° is outside the allowed range ({state.temp_min:g}–{state.temp_max:g}).")


def check(boost: Boost, state: AcState, now: float) -> str:
    """"boosting", or why it's over: "reached", "timeout" or "taken_over" (someone changed the
    power, mode or temperature, with the remote or anything else)."""
    if not state.power or state.mode != boost.mode or state.target_temp != boost.setpoint:
        return "taken_over"
    if state.current_temp is not None and reached(boost.mode, state.current_temp, boost.target):
        return "reached"
    if now - boost.started >= MAX_DURATION_S:
        return "timeout"
    return "boosting"


def finish_changes(boost: Boost, state: AcState) -> dict:
    """Changes that end a boost. Only settings still as the boost left them are touched, so
    anything changed by hand in the meantime stays."""
    changes: dict = {}
    if state.target_temp == boost.setpoint:
        changes["target_temp"] = boost.target
    if boost.restore_fan and boost.fan != boost.restore_fan and state.fan_speed == boost.fan:
        changes["fan_speed"] = boost.restore_fan
    if boost.restore_power_save and state.power_save is False:
        changes["power_save"] = True
    return changes


async def apply_finish(controller: AcController, boost: Boost, changes: dict, strict: bool = True) -> None:
    """Send `changes` from finish_changes (as one command, so the unit beeps once). With
    strict=False a failure is only logged (the unit may be off, which rejects some of them)."""
    try:
        await controller.apply(changes, mode=boost.mode)
    except Exception as error:
        if strict:
            raise
        _LOGGER.warning("Couldn't finish quick cool: %s", error)


def describe(boost: Boost | None, state: AcState) -> dict:
    """What the UI needs to show: whether a boost is running, its target, and its name."""
    active = boost is not None and check(boost, state, 0) != "taken_over"
    mode = boost.mode if active else boost_mode(state)
    return {
        "active": active,
        "target": boost.target if active else None,
        "label": label(mode),
        "available": mode is not None and state.current_temp is not None and state.temp_min is not None,
    }


class BoostStore:
    """The running boost, kept in a small JSON file."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> Boost | None:
        try:
            return Boost(**json.loads(self.path.read_text()))
        except (FileNotFoundError, ValueError, TypeError):
            return None

    def write(self, boost: Boost) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(boost)))
        tmp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def runner_alive(boost: Boost) -> bool:
    # Match the run ID too: after a restart the PID may belong to something else.
    try:
        return boost.run_id.encode() in Path(f"/proc/{boost.pid}/cmdline").read_bytes()
    except OSError:
        return False


def spawn_runner(run_id: str, demo: bool = False) -> int:
    """Start `lg-ac-cli boost-run` in the background, detached from this process."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    process = subprocess.Popen(
        [sys.executable, "-m", "lg_ac.cli", *(["--demo"] if demo else []), "boost-run", run_id],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=env,
    )
    return process.pid


class BoostService:
    """Starts, retargets and stops boosts; shared by the app and the command line."""

    def __init__(self, controller: AcController, store: BoostStore, spawn: Callable[[str], int]):
        self.controller = controller
        self.store = store
        self.spawn = spawn

    def current(self) -> Boost | None:
        """The running boost, restarting its background process if that died."""
        boost = self.store.read()
        if boost is not None and not runner_alive(boost):
            boost.pid = self.spawn(boost.run_id)
            self.store.write(boost)
        return boost

    async def start(self, target: float) -> Boost | None:
        """Boost to `target`. If a boost is already running, only its target changes."""
        state = await self.controller.refresh()
        running = self.current()
        if running is not None and check(running, state, time.time()) != "taken_over":
            check_target(state, target)
            running.target = target
            self.store.write(running)
            return running

        boost, changes = plan_start(state, target, time.time())
        if boost is not None:
            # Saved before the changes go out, so anything reading the state meanwhile
            # (e.g. a widget woken by LG's push) already sees the boost.
            boost.run_id = uuid.uuid4().hex
            self.store.write(boost)
        try:
            await self.controller.apply(changes)
        except BaseException:
            self.store.clear()
            raise
        if boost is not None:
            boost.pid = self.spawn(boost.run_id)
            self.store.write(boost)
        return boost

    async def stop(self, also: dict | None = None) -> None:
        """Stop boosting now and hold the target. `also` holds more changes to send in the same
        command, e.g. {"power": False}."""
        boost = self.store.read()
        if boost is not None:
            self.store.clear()
            if runner_alive(boost):
                os.kill(boost.pid, signal.SIGTERM)
        changes = dict(also or {})
        if boost is not None:
            changes = {**finish_changes(boost, await self.controller.refresh()), **changes}
        await self.controller.apply(changes, mode=boost.mode if boost else None)


async def run(
    controller: AcController,
    store: BoostStore,
    run_id: str,
    poll: float = POLL_S,
    near_poll: float = NEAR_POLL_S,
    sleep=asyncio.sleep,
    clock=time.time,
) -> str:
    """The background process: watch the room until the boost is over, then finish it.

    Returns why it ended: "reached", "timeout", "taken_over" or "stopped".
    """
    while True:
        boost = store.read()
        if boost is None or boost.run_id != run_id:
            return "stopped"  # stopped from elsewhere, or replaced by a newer boost
        try:
            state = await controller.refresh()
        except Exception as error:  # e.g. the network dropped; try again later
            _LOGGER.warning("Quick cool couldn't read the unit: %s", error)
            if clock() - boost.started < MAX_DURATION_S:
                await sleep(poll)
                continue
            store.clear()
            return "timeout"
        verdict = check(boost, state, clock())
        if verdict == "boosting":
            near = state.current_temp is not None and abs(state.current_temp - boost.target) <= 1
            await sleep(near_poll if near else poll)
            continue
        # Cleared first, so readers woken by the changes below already see it as over.
        store.clear()
        await apply_finish(controller, boost, finish_changes(boost, state), strict=False)
        return verdict
