import unittest
from dataclasses import replace

from lg_ac.controller import AcController
from lg_ac.demo import DemoApi
from lg_ac.presets import get_preset, plan, presets_for


class ExtrasTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = DemoApi()
        self.controller = AcController(self.api)
        device = (await self.controller.list_air_conditioners())[0]
        self.state = await self.controller.connect(device)

    async def test_reads_extras(self):
        s = self.state
        self.assertEqual((s.swing, s.power_save, s.display_light), (False, False, True))
        self.assertTrue(s.can_swing and s.can_power_save and s.can_display_light)
        self.assertEqual((s.filter_used_hours, s.filter_lifetime_hours, s.error), (720, None, False))
        self.assertEqual(s.timers, ("start", "stop", "sleep"))
        self.assertIsNone(s.timer_stop)

    async def test_toggles(self):
        await self.controller.set_swing(True)
        await self.controller.set_power_save(True)
        await self.controller.set_display_light(False)
        self.assertEqual(self.api.commands, [
            {"windDirection": {"rotateUpDown": True}},
            {"powerSave": {"powerSaveEnabled": True}},
            {"display": {"light": "OFF"}},
        ])
        s = await self.controller.refresh()
        self.assertEqual((s.swing, s.power_save, s.display_light), (True, True, False))

    async def test_timers(self):
        await self.controller.set_timer("stop", 90)
        await self.controller.set_timer("sleep", 120)
        await self.controller.set_timer("start", 45)
        self.assertEqual(self.api.commands[0], {"timer": {"relativeHourToStop": 1, "relativeMinuteToStop": 30}})
        self.assertEqual(self.api.commands[1], {"sleepTimer": {"relativeHourToStop": 2, "relativeMinuteToStop": 0}})
        self.assertEqual(self.api.commands[2], {"timer": {"relativeHourToStart": 0, "relativeMinuteToStart": 45}})
        s = await self.controller.refresh()
        self.assertEqual((s.timer_stop, s.timer_sleep, s.timer_start), (90, 120, 45))

    async def test_cancel_timer(self):
        await self.controller.set_timer("stop", 60)
        await self.controller.set_timer("stop", 0)
        self.assertEqual(self.api.commands[-1], {"timer": {"relativeStopTimer": "UNSET"}})
        self.assertIsNone((await self.controller.refresh()).timer_stop)

    async def test_timer_limits(self):
        for minutes in (-5, 24 * 60 + 1):
            with self.assertRaises(ValueError):
                await self.controller.set_timer("stop", minutes)
        with self.assertRaises(ValueError):
            await self.controller.set_timer("tomorrow", 30)
        self.assertEqual(self.api.commands, [])

    async def test_error_state(self):
        self.api.status["runState"]["currentState"] = "ERROR"
        self.assertTrue((await self.controller.refresh()).error)


class PresetTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = DemoApi()
        self.controller = AcController(self.api)
        device = (await self.controller.list_air_conditioners())[0]
        self.state = await self.controller.connect(device)

    def test_sleep_plan(self):
        changes = plan(get_preset("sleep"), self.state)
        self.assertEqual(changes, {"target_temp": 25, "fan_speed": "LOW", "display_light": False, "timer_sleep": 480})

    def test_max_cool_uses_coldest_allowed(self):
        changes = plan(get_preset("max_cool"), self.state)
        self.assertEqual(changes, {"target_temp": 18, "fan_speed": "HIGH", "swing": True})

    def test_plan_turns_on_and_switches_mode(self):
        state = replace(self.state, power=False, mode="FAN")
        changes = plan(get_preset("eco"), state)
        self.assertEqual(changes["power"], True)
        self.assertEqual(changes["mode"], "COOL")
        self.assertTrue(changes["can_set_temp"])
        self.assertEqual(list(changes)[:2], ["power", "mode"])

    def test_plan_skips_unsupported(self):
        state = replace(self.state, can_swing=False, timers=(), fan_speeds=["SILENT"])
        changes = plan(get_preset("sleep"), state)
        self.assertNotIn("swing", changes)
        self.assertNotIn("timer_sleep", changes)
        self.assertNotIn("fan_speed", changes)

    def test_unknown_preset(self):
        with self.assertRaises(ValueError):
            get_preset("party")

    async def test_apply_runs_in_order_and_lands(self):
        state = replace(self.state, power=False)
        self.api.status["operation"]["airConOperationMode"] = "POWER_OFF"
        self.api.status["airConJobMode"]["currentJobMode"] = "FAN"
        await self.controller.refresh()
        changes = plan(get_preset("sleep"), replace(state, mode="FAN"))
        await self.controller.apply(changes)
        self.assertEqual(self.api.commands[0], {"operation": {"airConOperationMode": "POWER_ON"}})
        self.assertEqual(self.api.commands[1], {"airConJobMode": {"currentJobMode": "COOL"}})
        self.assertEqual(self.api.commands[2], {"temperatureInUnits": {"coolTargetTemperature": 25, "unit": "C"}})
        s = await self.controller.refresh()
        self.assertEqual((s.power, s.mode, s.target_temp, s.fan_speed, s.display_light, s.timer_sleep),
                         (True, "COOL", 25, "LOW", False, 480))

    def test_presets_for_lists_all_with_changes(self):
        data = presets_for(self.state)
        self.assertEqual([p["id"] for p in data], ["sleep", "max_cool", "eco"])
        self.assertIn("changes", data[0])


if __name__ == "__main__":
    unittest.main()
