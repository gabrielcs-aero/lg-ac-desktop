"""Command-line backend for the panel widgets (KDE Plasma and GNOME Shell).

Every command prints one line of JSON: {"ok": true, ...} or {"ok": false, "error": "..."}.

    lg-ac-cli status
    lg-ac-cli power on|off
    lg-ac-cli mode COOL
    lg-ac-cli fan HIGH
    lg-ac-cli temp 23.5 [--mode COOL]
    lg-ac-cli swing on|off
    lg-ac-cli power-save on|off
    lg-ac-cli display on|off
    lg-ac-cli timer start|stop|sleep MINUTES     (0 cancels)
    lg-ac-cli set mode=COOL fan=HIGH temp=23      (several at once: the unit beeps once)
    lg-ac-cli preset sleep|max_cool|eco
    lg-ac-cli boost 23|off                        (quick cool to 23°, then hold it)
    lg-ac-cli watch [--timeout SECONDS]           (waits for a change pushed by LG)

Add --demo to talk to a simulated unit instead of the real one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import aiohttp
from thinqconnect import ThinQApi, ThinQAPIException

from . import boost
from .boost import Boost, BoostService, BoostStore
from .config import cache_dir, load_config
from .controller import AcController, AcState, DeviceInfo
from .demo import FileDemoApi
from .live import LiveUpdates, live_client_id
from .presets import get_preset, plan, presets_for
from .text import describe_error, pretty

def _on_off(value: str) -> bool:
    if value not in ("on", "off"):
        raise ValueError(f"Use on or off, not {value!r}.")
    return value == "on"


def _temperature(value: str) -> float:
    number = float(value)
    return int(number) if number.is_integer() else number


# `set` keys -> (AcState field, how to read the value)
_SET_FIELDS = {
    "power": ("power", _on_off),
    "mode": ("mode", str),
    "fan": ("fan_speed", str),
    "temp": ("target_temp", _temperature),
    "swing": ("swing", _on_off),
    "power-save": ("power_save", _on_off),
    "display": ("display_light", _on_off),
    "timer-start": ("timer_start", int),
    "timer-stop": ("timer_stop", int),
    "timer-sleep": ("timer_sleep", int),
}


def parse_set(fields: list[str]) -> dict:
    """["mode=COOL", "temp=23"] -> {"mode": "COOL", "target_temp": 23}"""
    changes = {}
    for item in fields:
        key, _, value = item.partition("=")
        if key not in _SET_FIELDS or not value:
            raise ValueError(f"Can't set {item!r}. Use KEY=VALUE with one of: {', '.join(_SET_FIELDS)}.")
        field, convert = _SET_FIELDS[key]
        changes[field] = convert(value)
    return changes


# Errors meaning the cached device is no longer valid for this account.
_STALE_DEVICE_CODES = {"1205", "1211", "1212", "1213", "1224"}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def state_to_json(device: DeviceInfo, state: AcState, running: Boost | None = None) -> dict:
    data = asdict(state)
    data["labels"] = {value: pretty(value) for value in [*state.modes, *state.fan_speeds]}
    data["alias"] = device.alias
    data["model_name"] = device.model_name
    data["presets"] = presets_for(state)
    data["boost"] = boost.describe(running, state)
    return data


async def _load_device(controller: AcController, wanted_id: str, cache_file: Path) -> DeviceInfo:
    """Set up the controller from the cached profile, fetching it only when needed."""
    cached = _read_json(cache_file)
    if cached and (not wanted_id or cached.get("device_id") == wanted_id):
        device = DeviceInfo(cached["device_id"], cached["alias"], cached["model_name"])
        await controller.load(device, cached["profile"])
        return device

    devices = await controller.list_air_conditioners()
    if not devices:
        raise RuntimeError("No air conditioner found on this LG account.")
    device = next((d for d in devices if d.device_id == wanted_id), devices[0])
    profile = await controller.load(device)
    _write_json(cache_file, {**asdict(device), "profile": profile})
    return device


async def run(args: argparse.Namespace) -> dict:
    cache = cache_dir()

    if args.demo:
        api = FileDemoApi(cache / "demo-status.json", latency=0.3)
        controller = AcController(api)
        device = await _load_device(controller, "", cache / "demo-device.json")
        service = _boost_service(controller, cache / "demo-boost.json", demo=True)
        if args.command == "watch":
            return await _watch_demo(controller, device, api, service, args.timeout)
        if args.command == "boost-run":
            api.latency = 0
            return {"ok": True, "ended": await boost.run(controller, service.store, args.run_id, poll=3, near_poll=2)}
        return await _execute(controller, device, service, args)

    config = load_config()
    if not config.is_complete:
        raise RuntimeError("Not set up yet. Open the LG AC app once and add your ThinQ token.")
    async with aiohttp.ClientSession() as session:
        api = ThinQApi(
            session=session,
            access_token=config.access_token,
            country_code=config.country_code,
            client_id=config.client_id,
        )
        controller = AcController(api)
        device_file = cache / "device.json"
        device = await _load_device(controller, config.device_id, device_file)
        service = _boost_service(controller, cache / "boost.json", demo=False)
        try:
            if args.command == "watch":
                return await _watch(controller, device, service, config, session, args.timeout)
            if args.command == "boost-run":
                return {"ok": True, "ended": await boost.run(controller, service.store, args.run_id)}
            return await _execute(controller, device, service, args)
        except ThinQAPIException as error:
            if str(error.code) in _STALE_DEVICE_CODES:
                device_file.unlink(missing_ok=True)
            raise


def _boost_service(controller: AcController, path: Path, demo: bool) -> BoostService:
    return BoostService(controller, BoostStore(path), spawn=lambda run_id: boost.spawn_runner(run_id, demo=demo))


async def _watch(controller: AcController, device: DeviceInfo, service: BoostService, config, session, timeout: float) -> dict:
    """Wait up to `timeout` seconds for LG to push a change, then report the state."""
    reports: list[dict] = []
    arrived = asyncio.Event()

    def on_report(report: dict) -> None:
        reports.append(report)
        arrived.set()

    live = LiveUpdates(
        session, config.access_token, config.country_code, live_client_id(config.client_id, "cli"),
        device.device_id, on_report, store_dir=cache_dir() / "mqtt-cli",
    )
    if not await live.start():
        raise RuntimeError("Live updates aren't available right now.")
    try:
        state = await controller.refresh()
        reports.clear()  # anything before the refresh is already included in it
        arrived.clear()
        try:
            await asyncio.wait_for(arrived.wait(), timeout)
        except TimeoutError:
            return {"ok": True, "changed": False, "state": state_to_json(device, state, service.store.read())}
        await asyncio.sleep(1)  # a single change often arrives as a few reports
        for report in reports:
            state = controller.apply_report(report)
        return {"ok": True, "changed": True, "state": state_to_json(device, state, service.store.read())}
    finally:
        await live.stop()


async def _watch_demo(controller: AcController, device: DeviceInfo, api: FileDemoApi, service: BoostService, timeout: float) -> dict:
    """--demo stand-in for live updates: wait for the simulated unit to change, whether from
    another command or its room warming up or cooling down."""
    def mtime():
        return api.path.stat().st_mtime_ns if api.path.exists() else 0

    api.latency = 0
    await controller.refresh()  # brings the simulated room up to date
    before = mtime()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(0.25)
        state = await controller.refresh()
        if mtime() != before:
            return {"ok": True, "changed": True, "state": state_to_json(device, state, service.store.read())}
    return {"ok": True, "changed": False, "state": state_to_json(device, await controller.refresh(), service.store.read())}


async def _execute(controller: AcController, device: DeviceInfo, service: BoostService, args: argparse.Namespace) -> dict:
    if args.command == "status":
        state = await controller.refresh()
        return {"ok": True, "state": state_to_json(device, state, service.current())}
    if args.command == "power":
        if args.value == "off" and service.store.read() is not None:
            # End the quick cool too, so turning it back on doesn't resume full blast.
            await service.stop(also={"power": False})
        else:
            await controller.set_power(args.value == "on")
    elif args.command == "mode":
        await controller.set_mode(args.value)
    elif args.command == "fan":
        await controller.set_fan_speed(args.value)
    elif args.command == "temp":
        if args.mode is None:
            await controller.refresh()  # need the current mode to pick the right setpoint
        value = int(args.value) if args.value.is_integer() else args.value
        await controller.set_target_temperature(value, mode=args.mode)
    elif args.command == "swing":
        await controller.set_swing(args.value == "on")
    elif args.command == "power-save":
        await controller.set_power_save(args.value == "on")
    elif args.command == "display":
        await controller.set_display_light(args.value == "on")
    elif args.command == "timer":
        await controller.set_timer(args.kind, args.minutes)
    elif args.command == "set":
        changes = parse_set(args.fields)
        if "target_temp" in changes and "mode" not in changes:
            await controller.refresh()  # need the current mode to pick the right setpoint
        if changes.get("power") is False and service.store.read() is not None:
            await service.stop(also=changes)  # end the quick cool in the same command
        else:
            await controller.apply(changes)
    elif args.command == "preset":
        preset = get_preset(args.value)
        await controller.apply(plan(preset, await controller.refresh()))
    elif args.command == "boost":
        if args.value == "off":
            await service.stop()
        else:
            try:
                target = float(args.value)
            except ValueError:
                raise ValueError("Give a temperature, e.g. `boost 23`, or `boost off`.") from None
            await service.start(int(target) if target.is_integer() else target)
    return {"ok": True}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lg-ac-cli", description="Control an LG ThinQ air conditioner.")
    parser.add_argument("--demo", action="store_true", help="use a simulated air conditioner")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="print the current state as JSON")
    commands.add_parser("power", help="turn on or off").add_argument("value", choices=["on", "off"])
    commands.add_parser("mode", help="set the mode, e.g. COOL").add_argument("value")
    commands.add_parser("fan", help="set the fan speed, e.g. HIGH").add_argument("value")
    temp = commands.add_parser("temp", help="set the target temperature")
    temp.add_argument("value", type=float)
    temp.add_argument("--mode", help="mode the temperature is for (default: the unit's current mode)")
    for name, help_text in (("swing", "up/down swing"), ("power-save", "power-saving mode"), ("display", "the unit's display light")):
        commands.add_parser(name, help=f"turn {help_text} on or off").add_argument("value", choices=["on", "off"])
    timer = commands.add_parser("timer", help="start a countdown timer (0 minutes cancels it)")
    timer.add_argument("kind", choices=["start", "stop", "sleep"], help="start: turn on, stop: turn off, sleep: sleep timer")
    timer.add_argument("minutes", type=int)
    commands.add_parser(
        "set", help="change several settings in one command, so the unit beeps once: e.g. mode=COOL fan=HIGH temp=23"
    ).add_argument("fields", nargs="+", metavar="KEY=VALUE",
                   help="keys: " + ", ".join(_SET_FIELDS) + " (on/off for switches, minutes for timers)")
    commands.add_parser("preset", help="apply a preset").add_argument("value")
    commands.add_parser(
        "boost", help="cool (or heat) to a temperature as fast as possible, then hold it; `off` stops"
    ).add_argument("value", help="target temperature, or off")
    commands.add_parser("boost-run").add_argument("run_id")  # internal: the process behind `boost`
    watch = commands.add_parser("watch", help="wait for a change pushed by LG, then print the state")
    watch.add_argument("--timeout", type=float, default=540, help="seconds to wait (default 540)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as error:  # report every failure as JSON so the widgets can show it
        result = {"ok": False, "error": describe_error(error)}
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
