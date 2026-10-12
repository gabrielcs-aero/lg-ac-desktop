"""Command-line backend for the panel widgets (KDE Plasma and GNOME Shell).

Every command prints one line of JSON: {"ok": true, ...} or {"ok": false, "error": "..."}.

    lg-ac-cli status
    lg-ac-cli power on|off
    lg-ac-cli mode COOL
    lg-ac-cli fan HIGH
    lg-ac-cli temp 23.5 [--mode COOL]

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

from .config import load_config
from .controller import AcController, AcState, DeviceInfo
from .demo import DemoApi
from .text import describe_error, pretty

# Errors meaning the cached device is no longer valid for this account.
_STALE_DEVICE_CODES = {"1205", "1211", "1212", "1213", "1224"}


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "lg-ac-desktop"


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


def state_to_json(device: DeviceInfo, state: AcState) -> dict:
    data = asdict(state)
    data["labels"] = {value: pretty(value) for value in [*state.modes, *state.fan_speeds]}
    data["alias"] = device.alias
    data["model_name"] = device.model_name
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
    demo_file = cache / "demo-status.json"

    if args.demo:
        api = DemoApi(latency=0.3)
        api.status = _read_json(demo_file) or api.status
        controller = AcController(api)
        device = await _load_device(controller, "", cache / "demo-device.json")
        result = await _execute(controller, device, args)
        _write_json(demo_file, api.status)
        return result

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
        try:
            return await _execute(controller, device, args)
        except ThinQAPIException as error:
            if str(error.code) in _STALE_DEVICE_CODES:
                device_file.unlink(missing_ok=True)
            raise


async def _execute(controller: AcController, device: DeviceInfo, args: argparse.Namespace) -> dict:
    if args.command == "status":
        return {"ok": True, "state": state_to_json(device, await controller.refresh())}
    if args.command == "power":
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
