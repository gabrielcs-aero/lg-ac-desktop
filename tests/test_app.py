import os
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

    def test_setup_screen_when_not_configured(self):
        model = AcModel(self.runner, Config(), demo=False)
        model.start()
        self.assertTrue(model.needsSetup)
        self.assertEqual(model.saveSetup("", "BR"), "Enter the token and a two-letter country code.")


if __name__ == "__main__":
    unittest.main()
