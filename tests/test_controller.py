import asyncio
import unittest

from lg_ac.controller import AcController
from lg_ac.demo import DemoApi


class ControllerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = DemoApi()
        self.controller = AcController(self.api)
        devices = await self.controller.list_air_conditioners()
        self.state = await self.controller.connect(devices[0])

    async def test_lists_only_air_conditioners(self):
        devices = await self.controller.list_air_conditioners()
        self.assertEqual([d.device_id for d in devices], ["test-device-id"])

    async def test_reads_state(self):
        s = self.state
        self.assertTrue(s.power)
        self.assertEqual(s.mode, "COOL")
        self.assertEqual(s.modes, ["COOL", "AIR_DRY", "FAN", "AUTO"])
        self.assertEqual((s.current_temp, s.target_temp, s.unit), (26, 22, "C"))
        self.assertEqual((s.temp_min, s.temp_max, s.temp_step), (18, 30, 0.5))
        self.assertTrue(s.can_set_temp)
        self.assertEqual(s.fan_speed, "MID")
        self.assertEqual(s.fan_speeds, ["NATURE", "LOW", "LOW_MID", "MID", "MID_HIGH", "HIGH"])

    async def test_power_off(self):
        await self.controller.set_power(False)
        self.assertEqual(self.api.commands, [{"operation": {"airConOperationMode": "POWER_OFF"}}])

    async def test_set_mode_and_fan(self):
        await self.controller.set_mode("AIR_DRY")
        await self.controller.set_fan_speed("HIGH")
        self.assertEqual(
            self.api.commands,
            [{"airConJobMode": {"currentJobMode": "AIR_DRY"}}, {"airFlow": {"windStrength": "HIGH"}}],
        )

    async def test_set_target_temperature_in_cool_mode(self):
        await self.controller.set_target_temperature(24.5)
        self.assertEqual(self.api.commands, [{"temperatureInUnits": {"coolTargetTemperature": 24.5, "unit": "C"}}])
        self.assertEqual((await self.controller.refresh()).target_temp, 24.5)

    async def test_set_target_temperature_in_auto_mode(self):
        await self.controller.set_mode("AUTO")
        await self.controller.refresh()
        await self.controller.set_target_temperature(23)
        self.assertEqual(self.api.commands[-1], {"temperatureInUnits": {"autoTargetTemperature": 23, "unit": "C"}})

    async def test_temperature_for_a_mode_not_yet_read_back(self):
        # The window sends a new mode and then a temperature before refreshing in between.
        await self.controller.set_mode("AUTO")
        await self.controller.set_target_temperature(23, mode="AUTO")
        self.assertEqual(self.api.commands[-1], {"temperatureInUnits": {"autoTargetTemperature": 23, "unit": "C"}})

    async def test_temp_modes(self):
        self.assertEqual(self.state.temp_modes, ("COOL", "AUTO"))

    async def test_commands_are_sent_in_order(self):
        self.api.delays = [0.05, 0, 0]  # the first request is slow; later ones must still wait for it
        await asyncio.gather(
            self.controller.set_power(False),
            self.controller.set_power(True),
            self.controller.set_fan_speed("HIGH"),
        )
        self.assertEqual(
            self.api.commands,
            [
                {"operation": {"airConOperationMode": "POWER_OFF"}},
                {"operation": {"airConOperationMode": "POWER_ON"}},
                {"airFlow": {"windStrength": "HIGH"}},
            ],
        )

    async def test_no_setpoint_in_fan_mode(self):
        await self.controller.set_mode("FAN")
        state = await self.controller.refresh()
        self.assertFalse(state.can_set_temp)
        self.assertEqual(state.target_temp, 22)  # still shown, just not adjustable
        with self.assertRaises(ValueError):
            await self.controller.set_target_temperature(24)

    async def test_rejects_off_step_temperature(self):
        with self.assertRaises(ValueError):
            await self.controller.set_target_temperature(24.25)

    async def test_rejects_out_of_range_temperature(self):
        with self.assertRaises(ValueError):
            await self.controller.set_target_temperature(35)
        self.assertEqual(self.api.commands, [])

    async def test_rejects_unsupported_fan_speed(self):
        with self.assertRaises(ValueError):
            await self.controller.set_fan_speed("TURBO")
        self.assertEqual(self.api.commands, [])

    async def test_state_follows_commands(self):
        await self.controller.set_target_temperature(20)
        self.assertEqual((await self.controller.refresh()).target_temp, 20)
        await self.controller.set_power(False)
        self.assertFalse((await self.controller.refresh()).power)

    async def test_refresh_picks_up_changes(self):
        self.api.status["operation"]["airConOperationMode"] = "POWER_OFF"
        self.api.status["temperatureInUnits"][0]["targetTemperature"] = 25
        state = await self.controller.refresh()
        self.assertFalse(state.power)
        self.assertEqual(state.target_temp, 25)


if __name__ == "__main__":
    unittest.main()
