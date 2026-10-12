import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from lg_ac import cli


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(argv))
    return code, json.loads(out.getvalue())


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        env = {"XDG_CACHE_HOME": f"{self.dir.name}/cache", "XDG_CONFIG_HOME": f"{self.dir.name}/config"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def test_demo_status(self):
        code, out = run_cli("--demo", "status")
        self.assertEqual(code, 0)
        state = out["state"]
        self.assertEqual((state["power"], state["mode"], state["target_temp"]), (True, "COOL", 22))
        self.assertEqual(state["labels"]["AIR_DRY"], "Dry")
        self.assertEqual(state["labels"]["LOW_MID"], "Low mid")
        self.assertEqual(state["temp_modes"], ["COOL", "AUTO"])

    def test_demo_commands_persist_between_runs(self):
        self.assertEqual(run_cli("--demo", "power", "off"), (0, {"ok": True}))
        self.assertEqual(run_cli("--demo", "mode", "AUTO")[0], 0)
        self.assertEqual(run_cli("--demo", "temp", "24.5", "--mode", "AUTO")[0], 0)
        self.assertEqual(run_cli("--demo", "fan", "HIGH")[0], 0)
        state = run_cli("--demo", "status")[1]["state"]
        self.assertEqual(
            (state["power"], state["mode"], state["target_temp"], state["fan_speed"]),
            (False, "AUTO", 24.5, "HIGH"),
        )

    def test_temp_without_mode_uses_current_mode(self):
        self.assertEqual(run_cli("--demo", "temp", "25")[0], 0)
        self.assertEqual(run_cli("--demo", "status")[1]["state"]["target_temp"], 25)

    def test_extras_and_timers(self):
        for argv in (("swing", "on"), ("power-save", "on"), ("display", "off"), ("timer", "stop", "90")):
            self.assertEqual(run_cli("--demo", *argv), (0, {"ok": True}), argv)
        state = run_cli("--demo", "status")[1]["state"]
        self.assertEqual((state["swing"], state["power_save"], state["display_light"], state["timer_stop"]),
                         (True, True, False, 90))
        self.assertEqual(run_cli("--demo", "timer", "stop", "0")[0], 0)
        self.assertIsNone(run_cli("--demo", "status")[1]["state"]["timer_stop"])

    def test_preset(self):
        presets = run_cli("--demo", "status")[1]["state"]["presets"]
        sleep = next(p for p in presets if p["id"] == "sleep")
        self.assertEqual(sleep["changes"]["timer_sleep"], 480)
        self.assertEqual(run_cli("--demo", "preset", "sleep"), (0, {"ok": True}))
        state = run_cli("--demo", "status")[1]["state"]
        self.assertEqual((state["target_temp"], state["fan_speed"], state["timer_sleep"]), (25, "LOW", 480))
        code, out = run_cli("--demo", "preset", "party")
        self.assertEqual(code, 1)
        self.assertIn("Unknown preset", out["error"])

    def test_invalid_value_is_reported_as_json(self):
        code, out = run_cli("--demo", "fan", "TURBO")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])
        self.assertIn("TURBO", out["error"])

    def test_not_configured(self):
        code, out = run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("add your ThinQ token", out["error"])


if __name__ == "__main__":
    unittest.main()
