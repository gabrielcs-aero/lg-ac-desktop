import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from lg_ac import app as app_module
from lg_ac.app import AcModel, AsyncRunner, create_engine
from lg_ac.config import Config

QQuickStyle.setStyle("Basic")
qt_app = QGuiApplication.instance() or QGuiApplication([])


def wait_until(predicate, timeout_ms=3000):
    loop = QEventLoop()
    poll = QTimer(interval=20, timeout=lambda: predicate() and loop.quit())
    QTimer.singleShot(timeout_ms, loop.quit)
    poll.start()
    loop.exec()
    poll.stop()
    return predicate()


class AppTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app_module, "DEMO_LATENCY_S", 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The demo unit lives in the cache folder; keep the tests away from the real one.
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.dir.name})
        env.start()
        self.addCleanup(env.stop)
        self.runner = AsyncRunner()
        self.model = AcModel(self.runner, Config(), demo=True)
        self.engine = create_engine(self.model)
        self.warnings = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.window = self.engine.rootObjects()[0]

    def tearDown(self):
        self.model.close()
        del self.engine
        self.runner.stop()

    def test_window_loads_and_shows_demo_unit(self):
        self.model.start()
        self.assertTrue(wait_until(lambda: self.model.ready))
        self.assertEqual(self.window.property("title"), "Air conditioner (demo)")
        self.assertEqual(self.model.ac["target_temp"], 22)
        self.assertEqual(self.warnings, [])

    def test_changes_show_immediately_then_confirm(self):
        self.model.start()
        wait_until(lambda: self.model.ready)
        self.model.setMode("AUTO")
        self.model.setFanSpeed("HIGH")
        self.assertEqual((self.model.ac["mode"], self.model.ac["fan_speed"]), ("AUTO", "HIGH"))
        self.assertEqual(self.model.message, "Sending…")
        self.assertTrue(wait_until(lambda: self.model.message == "Sent"))
        self.assertEqual(self.warnings, [])

    def test_extras_and_presets_show_immediately(self):
        self.model.start()
        wait_until(lambda: self.model.ready)
        self.assertEqual(self.model.ac["timers"], ["start", "stop", "sleep"])  # lists, not tuples, for QML
        self.model.setSwing(True)
        self.model.setTimer("stop", 90)
        self.assertEqual((self.model.ac["swing"], self.model.ac["timer_stop"]), (True, 90))
        self.model.applyPreset("eco")
        self.assertEqual((self.model.ac["target_temp"], self.model.ac["power_save"]), (26, True))
        self.assertTrue(wait_until(lambda: self.model.message == "Sent"))
        self.model.refresh()
        self.assertTrue(wait_until(lambda: self.model.message == "Up to date"))
        self.assertEqual((self.model.ac["swing"], self.model.ac["timer_stop"], self.model.ac["power_save"]), (True, 90, True))
        self.assertEqual(self.warnings, [])

    def test_quick_cool(self):
        self.model.start()
        wait_until(lambda: self.model.ready)
        with mock.patch.object(app_module.boost, "spawn_runner", return_value=0):
            self.model.startBoost()  # room 26°, dial at 22°
            ac = self.model.ac
            self.assertEqual((ac["boost"]["active"], ac["shown_target"], ac["target_temp"], ac["fan_speed"]), (True, 22, 18, "HIGH"))
            self.assertTrue(wait_until(lambda: self.model.message == "Sent"))
            self.model.nudge(1)  # moves the quick cool's target, not the unit's setting
            self.assertEqual((self.model.ac["shown_target"], self.model.ac["target_temp"]), (22.5, 18))
            self.assertTrue(wait_until(lambda: self.model.ac["boost"]["target"] == 22.5 and self.model.message == "Sent"))
            self.model.stopBoost()
            ac = self.model.ac
            self.assertEqual((ac["boost"]["active"], ac["target_temp"], ac["fan_speed"]), (False, 22.5, "MID"))
            self.assertTrue(wait_until(lambda: self.model.message == "Sent"))
        self.assertEqual(self.warnings, [])

    def test_setup_screen_when_not_configured(self):
        model = AcModel(self.runner, Config(), demo=False)
        model.start()
        self.assertTrue(model.needsSetup)
        self.assertEqual(model.saveSetup("", "BR"), "Enter the token and a two-letter country code.")


if __name__ == "__main__":
    unittest.main()
