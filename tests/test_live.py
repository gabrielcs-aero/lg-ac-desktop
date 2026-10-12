import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import aiohttp

from lg_ac.controller import AcController
from lg_ac.demo import DemoApi
from lg_ac.live import LiveUpdates, live_client_id, parse_message


class FakeMqttClient:
    """Stands in for thinqconnect's ThinQMQTTClient."""

    instances = []
    fail_cached_connect = False

    def __init__(self, thinq_api, client_id, on_message_received, **callbacks):
        self.client_id = client_id
        self.on_message_received = on_message_received
        self.prepared = 0
        self.is_connected = False
        self.bytes_certificate = self.bytes_private_key = self.bytes_root_ca = None
        self.topic_subscription = None
        FakeMqttClient.instances.append(self)

    def __await__(self):
        async def init():
            return self
        return init().__await__()

    async def async_prepare_mqtt(self):
        self.prepared += 1
        self.bytes_certificate, self.bytes_private_key, self.bytes_root_ca = b"cert", b"key", b"ca"
        self.topic_subscription = "app/clients/x/feed"
        return True

    async def async_connect_mqtt(self):
        self.is_connected = not (FakeMqttClient.fail_cached_connect and self.prepared == 0)


class ParseTest(unittest.TestCase):
    def test_status_report(self):
        payload = json.dumps({"pushType": "DEVICE_STATUS", "deviceId": "d1", "report": {"powerSave": {"powerSaveEnabled": True}}})
        self.assertEqual(parse_message(payload.encode(), "d1"), {"powerSave": {"powerSaveEnabled": True}})

    def test_ignores_other_devices_and_types(self):
        other = json.dumps({"pushType": "DEVICE_STATUS", "deviceId": "d2", "report": {"x": {}}}).encode()
        push = json.dumps({"pushType": "DEVICE_PUSH", "deviceId": "d1", "pushCode": "WATER_IS_FULL"}).encode()
        self.assertIsNone(parse_message(other, "d1"))
        self.assertIsNone(parse_message(push, "d1"))
        self.assertIsNone(parse_message(b"not json", "d1"))

    def test_client_ids_differ_per_role(self):
        self.assertNotEqual(live_client_id("abc", "app"), live_client_id("abc", "cli"))
        self.assertEqual(live_client_id("abc", "app"), live_client_id("abc", "app"))


class LiveUpdatesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Path(self.dir.name) / "mqtt"
        self.session = aiohttp.ClientSession()
        self.reports = []
        FakeMqttClient.instances.clear()
        FakeMqttClient.fail_cached_connect = False

    async def asyncTearDown(self):
        await self.session.close()
        self.dir.cleanup()

    def make(self):
        live = LiveUpdates(self.session, "token", "BR", "client", "d1", self.reports.append, self.store,
                           client_factory=FakeMqttClient)

        async def no_subscribe(device_id):
            return {}
        live.api.async_post_event_subscribe = no_subscribe
        return live

    async def test_first_start_registers_and_caches_privately(self):
        self.assertTrue(await self.make().start())
        self.assertEqual(FakeMqttClient.instances[-1].prepared, 1)
        self.assertEqual(stat.S_IMODE(os.stat(self.store / "private-key.pem").st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.store).st_mode), 0o700)

    async def test_second_start_reuses_certificate(self):
        await self.make().start()
        await self.make().start()
        self.assertEqual(FakeMqttClient.instances[-1].prepared, 0)
        self.assertEqual(FakeMqttClient.instances[-1].bytes_certificate, b"cert")

    async def test_stale_certificate_is_replaced(self):
        await self.make().start()
        FakeMqttClient.fail_cached_connect = True
        self.assertTrue(await self.make().start())
        self.assertEqual(FakeMqttClient.instances[-1].prepared, 1)

    async def test_messages_reach_the_loop(self):
        live = self.make()
        await live.start()
        payload = json.dumps({"pushType": "DEVICE_STATUS", "deviceId": "d1", "report": {"display": {"light": "OFF"}}})
        await asyncio.to_thread(live._on_message, topic="t", payload=payload.encode(), dup=False, qos=1, retain=False)
        await asyncio.sleep(0.01)
        self.assertEqual(self.reports, [{"display": {"light": "OFF"}}])


    async def test_missing_aws_library_means_no_live_updates(self):
        live = LiveUpdates(self.session, "token", "BR", "client", "d1", self.reports.append, self.store)

        def missing():
            raise ImportError("No module named 'awscrt'")
        live._default_factory = missing
        self.assertFalse(await live.start())


class ApplyReportTest(unittest.IsolatedAsyncioTestCase):
    async def test_partial_report_merges_into_state(self):
        controller = AcController(DemoApi())
        await controller.connect((await controller.list_air_conditioners())[0])
        state = controller.apply_report({"operation": {"airConOperationMode": "POWER_OFF"},
                                         "timer": {"relativeStopTimer": "SET", "relativeHourToStop": 2, "relativeMinuteToStop": 0}})
        self.assertFalse(state.power)
        self.assertEqual(state.timer_stop, 120)
        self.assertEqual(state.mode, "COOL")  # untouched fields keep their value
        self.assertEqual(state.fan_speed, "MID")


if __name__ == "__main__":
    unittest.main()
