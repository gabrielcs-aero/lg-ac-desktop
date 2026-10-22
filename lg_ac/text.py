"""User-facing names and error messages, shared by the app, the CLI and the panel widgets."""

from __future__ import annotations

import aiohttp
from thinqconnect import ThinQAPIException

LABELS = {
    "COOL": "Cool",
    "HEAT": "Heat",
    "AUTO": "Auto",
    "AIR_DRY": "Dry",
    "FAN": "Fan",
    "AIR_CLEAN": "Air clean",
    "ENERGY_SAVING": "Energy saving",
}

# Codes from thinqconnect.ThinQAPIErrorCodes that the user can do something about.
ERROR_HINTS = {
    "1103": "check your token in Settings.",
    "1218": "check your token in Settings.",
    "1302": "check your token in Settings.",
    "1307": "check the country code in Settings.",
    "1308": "the token lacks control permission; create one that allows controlling devices.",
    "1222": "the air conditioner is offline. Check its Wi-Fi.",
    "2208": "the unit didn't take the command, even after retrying. Try again in a moment.",
    "2209": "the unit is responding slowly. Try again in a moment.",
    "2301": "turn on remote control for this unit in the ThinQ app.",
    "2304": "turn the air conditioner on first.",
    "2305": "this can't be changed in the current mode.",
}


def pretty(value: str) -> str:
    return LABELS.get(value) or value.replace("_", " ").capitalize()


def describe_error(error: BaseException) -> str:
    if isinstance(error, ThinQAPIException):
        message = f"LG ThinQ said: {error.message} ({error.code})"
        hint = ERROR_HINTS.get(str(error.code))
        return f"{message} — {hint}" if hint else message
    if isinstance(error, aiohttp.ClientError):
        return f"Network problem: {error}"
    return str(error) or type(error).__name__
