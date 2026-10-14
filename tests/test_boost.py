import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from lg_ac import boost, cli
from lg_ac.boost import BoostService, BoostStore
from lg_ac.controller import AcController, DeviceInfo
from lg_ac.demo import DemoApi, FileDemoApi

DEVICE = DeviceInfo("test-device-id", "Air conditioner", "S3NQ09AM31A")


def run(coro):
    return asyncio.run(coro)


class FakeTime:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class PlanTest(unittest.TestCase):
    def setUp(self):
        async def load():
            controller = AcController(DemoApi())
            return await controller.connect(DEVICE)

        self.state = run(load())  # cooling, room 26°, set to 22°, fan MID

    def test_runs_flat_out_until_the_target(self):
        planned, changes = boost.plan_start(self.state, 22, now=0)
        self.assertEqual(changes, {"target_temp": 18, "fan_speed": "HIGH"})
        self.assertEqual((planned.target, planned.setpoint, planned.fan, planned.restore_fan), (22, 18, "HIGH", "MID"))

    def test_turns_on_switches_to_cool_and_pauses_power_save(self):
        state = replace(self.state, power=False, mode="FAN", power_save=True)
        planned, changes = boost.plan_start(state, 22, now=0)
        self.assertEqual(changes["power"], True)
        self.assertEqual(changes["mode"], "COOL")
        self.assertEqual(changes["power_save"], False)
        self.assertTrue(planned.restore_power_save)

    def test_nothing_to_boost_when_the_room_is_already_there(self):
        planned, changes = boost.plan_start(self.state, 27, now=0)
        self.assertIsNone(planned)
        self.assertEqual(changes, {"target_temp": 27})

    def test_rejects_targets_out_of_range(self):
        with self.assertRaisesRegex(ValueError, "outside the allowed range"):
            boost.plan_start(self.state, 12, now=0)

    def test_check(self):
        planned, changes = boost.plan_start(self.state, 22, now=0)
        boosting = replace(self.state, **changes)
        self.assertEqual(boost.check(planned, boosting, now=60), "boosting")
        self.assertEqual(boost.check(planned, replace(boosting, current_temp=22), now=60), "reached")
        self.assertEqual(boost.check(planned, boosting, now=boost.MAX_DURATION_S), "timeout")
        # Someone set a temperature, switched mode or turned it off: they're in charge now.
        self.assertEqual(boost.check(planned, replace(boosting, target_temp=24), now=60), "taken_over")
        self.assertEqual(boost.check(planned, replace(boosting, mode="FAN"), now=60), "taken_over")
        self.assertEqual(boost.check(planned, replace(boosting, power=False), now=60), "taken_over")

    def test_finish_keeps_what_was_changed_by_hand(self):
        planned, changes = boost.plan_start(replace(self.state, power_save=True), 22, now=0)
        boosting = replace(self.state, **changes)
        self.assertEqual(boost.finish_changes(planned, boosting),
                         {"target_temp": 22, "fan_speed": "MID", "power_save": True})
        self.assertEqual(boost.finish_changes(planned, replace(boosting, fan_speed="LOW")),
                         {"target_temp": 22, "power_save": True})


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.time = FakeTime()
        self.api = DemoApi(simulate_room=True, clock=self.time)
        self.controller = AcController(self.api)
        self.store = BoostStore(Path(self.dir.name) / "boost.json")
        self.spawned = []
        self.service = BoostService(self.controller, self.store, spawn=lambda run_id: self.spawned.append(run_id) or 4242)
        run(self.controller.connect(DEVICE))

    def test_boost_reaches_the_target_then_holds_it(self):
        async def scenario():
            started = await self.service.start(22)
            self.assertEqual(self.spawned, [started.run_id])
            state = await self.controller.refresh()
            self.assertEqual((state.target_temp, state.fan_speed), (18, "HIGH"))
            ended = await boost.run(self.controller, self.store, started.run_id, sleep=self.time.sleep, clock=self.time)
            return ended, await self.controller.refresh()

        with mock.patch.object(boost, "MAX_DURATION_S", 10**9):
            ended, state = run(scenario())
        self.assertEqual(ended, "reached")
        self.assertLessEqual(state.current_temp, 22)
        self.assertEqual((state.target_temp, state.fan_speed), (22, "MID"))
        self.assertIsNone(self.store.read())

    def test_a_running_boost_only_changes_its_target(self):
        async def scenario():
            first = await self.service.start(22)
            with mock.patch.object(boost, "runner_alive", return_value=True):
                second = await self.service.start(23)
            return first, second

        first, second = run(scenario())
        self.assertEqual(second.run_id, first.run_id)
        self.assertEqual(self.store.read().target, 23)
        self.assertEqual(len(self.spawned), 1)

    def test_stop_holds_the_target(self):
        async def scenario():
            await self.service.start(22)
            await self.service.stop()
            return await self.controller.refresh()

        state = run(scenario())
        self.assertEqual((state.target_temp, state.fan_speed), (22, "MID"))
        self.assertIsNone(self.store.read())

    def test_runner_stops_when_the_boost_is_cancelled_elsewhere(self):
        async def scenario():
            started = await self.service.start(22)
            self.store.clear()
            return await boost.run(self.controller, self.store, started.run_id, sleep=self.time.sleep, clock=self.time)

        self.assertEqual(run(scenario()), "stopped")

    def test_runner_hands_over_when_someone_sets_a_temperature(self):
        async def scenario():
            started = await self.service.start(22)
            await self.controller.set_target_temperature(25)
            ended = await boost.run(self.controller, self.store, started.run_id, sleep=self.time.sleep, clock=self.time)
            return ended, await self.controller.refresh()

        ended, state = run(scenario())
        self.assertEqual(ended, "taken_over")
        self.assertEqual((state.target_temp, state.fan_speed), (25, "MID"))  # their temperature stays

    def test_a_dead_runner_is_restarted(self):
        run(self.service.start(22))
        self.assertIsNotNone(self.service.current())  # PID 4242 isn't our runner, so start another
        self.assertEqual(len(self.spawned), 2)


class DemoRoomTest(unittest.TestCase):
    def test_room_cools_toward_the_setpoint_and_warms_when_off(self):
        clock = FakeTime()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "demo.json"

            async def scenario():
                controller = AcController(FileDemoApi(path, clock=clock))
                start = (await controller.connect(DEVICE)).current_temp
                clock.now += 60
                cooled = (await controller.refresh()).current_temp
                await controller.set_power(False)
                clock.now += 600
                # A second process sees the same unit.
                warmed = (await AcController(FileDemoApi(path, clock=clock)).connect(DEVICE)).current_temp
                return start, cooled, warmed

            start, cooled, warmed = run(scenario())
        self.assertEqual(start, 26)
        self.assertLess(cooled, start)
        self.assertGreaterEqual(cooled, 22)
        self.assertGreater(warmed, cooled)


class CliBoostTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": f"{self.dir.name}/cache", "XDG_CONFIG_HOME": f"{self.dir.name}/config"})
        env.start()
        self.addCleanup(env.stop)
        spawn = mock.patch.object(boost, "spawn_runner", return_value=0)
        self.spawn = spawn.start()
        self.addCleanup(spawn.stop)

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--demo", *argv])
        return code, json.loads(out.getvalue())

    def test_boost_and_stop(self):
        self.assertEqual(self.cli("status")[1]["state"]["boost"],
                         {"active": False, "target": None, "label": "Quick cool", "available": True})
        self.assertEqual(self.cli("boost", "20"), (0, {"ok": True}))
        state = self.cli("status")[1]["state"]
        self.assertEqual(state["boost"]["active"], True)
        self.assertEqual(state["boost"]["target"], 20)
        self.assertEqual((state["target_temp"], state["fan_speed"]), (18, "HIGH"))

        self.assertEqual(self.cli("boost", "off"), (0, {"ok": True}))
        state = self.cli("status")[1]["state"]
        self.assertEqual(state["boost"]["active"], False)
        self.assertEqual((state["target_temp"], state["fan_speed"]), (20, "MID"))

    def test_turning_off_ends_the_boost(self):
        self.cli("boost", "20")
        self.assertEqual(self.cli("power", "off"), (0, {"ok": True}))
        state = self.cli("status")[1]["state"]
        self.assertEqual((state["power"], state["boost"]["active"], state["target_temp"]), (False, False, 20))

    def test_bad_values(self):
        code, out = self.cli("boost", "warm")
        self.assertEqual((code, out["ok"]), (1, False))
        self.assertIn("boost 23", out["error"])
        code, out = self.cli("boost", "5")
        self.assertIn("outside the allowed range", out["error"])


if __name__ == "__main__":
    unittest.main()
