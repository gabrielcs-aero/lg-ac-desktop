"""PySide6 desktop window for controlling the air conditioner."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
import threading
from typing import Any, Callable, Coroutine

import aiohttp
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from thinqconnect import ThinQApi, ThinQAPIException

from .config import Config, load_config, save_config
from .controller import AcController, AcState, DeviceInfo
from .demo import DemoApi

POLL_INTERVAL_MS = 60_000
TEMPERATURE_SEND_DELAY_MS = 700
# How long after the last command to read the unit back, giving it time to apply the change.
SETTLE_MS = 4_000
DEMO_LATENCY_S = 1.0
TOKEN_URL = "https://connect-pat.lgthinq.com"

STYLE = """
QPushButton { padding: 6px 10px; border: 1px solid palette(mid); border-radius: 6px; background: palette(button); }
QPushButton:hover { border-color: #2563eb; }
QPushButton:checked { background: #2563eb; color: white; border-color: #2563eb; }
QPushButton:disabled { color: palette(mid); }
QPushButton#power { font-size: 15px; font-weight: bold; }
QPushButton#power:checked { background: #16a34a; border-color: #16a34a; }
QLabel#section { color: #6b7280; font-weight: bold; margin-top: 6px; }
"""

MODE_LABELS = {
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
    "2301": "turn on remote control for this unit in the ThinQ app.",
    "2304": "turn the air conditioner on first.",
    "2305": "this can't be changed in the current mode.",
}


def pretty(value: str) -> str:
    return MODE_LABELS.get(value) or value.replace("_", " ").capitalize()


class AsyncRunner(QObject):
    """Runs coroutines on a background asyncio loop and reports back on the Qt thread."""

    _finished = Signal(object, object, object)  # callback, result, error

    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()
        self._finished.connect(self._deliver)

    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def done(f):
            error = f.exception()
            callback = on_error if error else on_done
            self._finished.emit(callback, None if error else f.result(), error)

        future.add_done_callback(done)

    def run_sync(self, coro: Coroutine[Any, Any, Any], timeout: float = 5) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=2)

    @staticmethod
    def _deliver(callback, result, error) -> None:
        if callback:
            callback(error if error is not None else result)


class SetupDialog(QDialog):
    def __init__(self, config: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Connect to LG ThinQ")
        self.setMinimumWidth(460)

        intro = QLabel(
            "This app uses LG's official ThinQ Connect API.<br><br>"
            f"1. Open <a href='{TOKEN_URL}'>{TOKEN_URL}</a> and sign in with the same LG account "
            "you use in the ThinQ phone app.<br>"
            "2. Create a Personal Access Token with permission to list devices, read device status "
            "and control devices.<br>"
            "3. Paste the token below."
        )
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        intro.setTextFormat(Qt.RichText)

        self.token = QLineEdit(config.access_token)
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("Personal Access Token")
        self.country = QLineEdit(config.country_code)
        self.country.setMaxLength(2)
        self.country.setPlaceholderText("Two-letter country code, e.g. BR")

        form = QFormLayout()
        form.addRow("Token", self.token)
        form.addRow("Country", self.country)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str]:
        return self.token.text().strip(), self.country.text().strip().upper()


class MainWindow(QMainWindow):
    def __init__(self, runner: AsyncRunner, config: Config, demo: bool = False):
        super().__init__()
        self.runner = runner
        self.config = config
        self.demo = demo
        self.session: aiohttp.ClientSession | None = None
        self.controller: AcController | None = None
        self.devices: list[DeviceInfo] = []
        self.state: AcState | None = None
        self.pending_target: float | None = None
        self.in_flight = 0  # commands sent but not yet acknowledged
        self.generation = 0  # bumped on every user change, so stale refreshes can be dropped
        self.error_shown = False

        self.setWindowTitle("Air Conditioner")
        self.setMinimumWidth(420)
        self._build_ui()

        self.poll_timer = QTimer(self, interval=POLL_INTERVAL_MS, timeout=self.refresh)
        self.temp_timer = QTimer(self, singleShot=True, interval=TEMPERATURE_SEND_DELAY_MS, timeout=self._send_target)
        self.settle_timer = QTimer(self, singleShot=True, interval=SETTLE_MS, timeout=self.refresh)

    # ---------- layout ----------

    def _build_ui(self) -> None:
        settings = QAction("Change token…", self, triggered=self.open_setup)
        self.menuBar().addMenu("&Settings").addAction(settings)

        self.device_picker = QComboBox()
        self.device_picker.currentIndexChanged.connect(self._device_chosen)

        self.power_button = QPushButton("Power")
        self.power_button.setObjectName("power")
        self.power_button.setCheckable(True)
        self.power_button.setMinimumHeight(48)
        self.power_button.clicked.connect(self._toggle_power)

        self.current_label = QLabel("Room: —")
        self.current_label.setAlignment(Qt.AlignCenter)

        self.target_label = QLabel("—")
        big = QFont()
        big.setPointSize(40)
        big.setBold(True)
        self.target_label.setFont(big)
        self.target_label.setAlignment(Qt.AlignCenter)

        self.down_button = QPushButton("−")
        self.up_button = QPushButton("+")
        for button, step in ((self.down_button, -1), (self.up_button, 1)):
            button.setFixedSize(56, 56)
            button.setFont(QFont(button.font().family(), 20))
            button.clicked.connect(lambda _=False, s=step: self._nudge_target(s))

        temp_row = QHBoxLayout()
        temp_row.addWidget(self.down_button)
        temp_row.addWidget(self.target_label, 1)
        temp_row.addWidget(self.up_button)

        self.mode_row = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.idClicked.connect(self._mode_clicked)

        self.fan_row = QHBoxLayout()
        self.fan_group = QButtonGroup(self)
        self.fan_group.setExclusive(True)
        self.fan_group.idClicked.connect(self._fan_clicked)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(self.device_picker)
        layout.addWidget(self.power_button)
        layout.addSpacing(8)
        layout.addWidget(self.current_label)
        layout.addLayout(temp_row)
        layout.addSpacing(8)
        layout.addWidget(self._section("Mode"))
        layout.addLayout(self.mode_row)
        layout.addWidget(self._section("Fan speed"))
        layout.addLayout(self.fan_row)
        layout.addStretch(1)
        footer = QHBoxLayout()
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.refresh_button)
        layout.addLayout(footer)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self._set_controls_enabled(False)

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    @staticmethod
    def _fill_choices(row: QHBoxLayout, group: QButtonGroup, values: list[str]) -> None:
        for button in group.buttons():
            group.removeButton(button)
            button.deleteLater()
        for i, value in enumerate(values):
            button = QPushButton(pretty(value))
            button.setCheckable(True)
            button.setProperty("value", value)
            group.addButton(button, i)
            row.addWidget(button)

    # ---------- connection ----------

    def start(self) -> None:
        if not self.demo and not self.config.is_complete:
            if not self.open_setup():
                self.status("Add a ThinQ token from Settings to get started.")
            return
        self.connect_api()

    def open_setup(self) -> bool:
        dialog = SetupDialog(self.config, self)
        if dialog.exec() != QDialog.Accepted:
            return False
        self.demo = False
        token, country = dialog.values()
        if not token or len(country) != 2:
            QMessageBox.warning(self, "Missing details", "Enter both the token and a two-letter country code.")
            return False
        self.config.access_token, self.config.country_code = token, country
        save_config(self.config)
        self.connect_api()
        return True

    def connect_api(self) -> None:
        self.poll_timer.stop()
        self.status("Connecting to LG ThinQ…")
        self._set_controls_enabled(False)
        self.runner.submit(self._open_api(), self._devices_loaded, self._show_error)

    async def _open_api(self) -> list[DeviceInfo]:
        if self.demo:
            self.controller = AcController(DemoApi(latency=DEMO_LATENCY_S))
            return await self.controller.list_air_conditioners()
        if self.session:
            await self.session.close()
        self.session = aiohttp.ClientSession()
        api = ThinQApi(
            session=self.session,
            access_token=self.config.access_token,
            country_code=self.config.country_code,
            client_id=self.config.client_id,
        )
        self.controller = AcController(api)
        return await self.controller.list_air_conditioners()

    def _devices_loaded(self, devices: list[DeviceInfo]) -> None:
        self.devices = devices
        if not devices:
            self.status("No air conditioner found on this LG account. Is it set up in the ThinQ app?")
            return
        self.device_picker.blockSignals(True)
        self.device_picker.clear()
        for d in devices:
            self.device_picker.addItem(f"{d.alias} ({d.model_name})" if d.model_name else d.alias)
        self.device_picker.setVisible(len(devices) > 1)
        saved = next((i for i, d in enumerate(devices) if d.device_id == self.config.device_id), 0)
        self.device_picker.setCurrentIndex(saved)
        self.device_picker.blockSignals(False)
        self._device_chosen(saved)

    def _device_chosen(self, index: int) -> None:
        if not (0 <= index < len(self.devices)) or self.controller is None:
            return
        device = self.devices[index]
        if not self.demo and self.config.device_id != device.device_id:
            self.config.device_id = device.device_id
            save_config(self.config)
        self.setWindowTitle(f"{device.alias} (demo)" if self.demo else device.alias)
        self.status(f"Loading {device.alias}…")
        self.runner.submit(self.controller.connect(device), self._connected, self._show_error)

    def _connected(self, state: AcState) -> None:
        self._fill_choices(self.mode_row, self.mode_group, state.modes)
        self._fill_choices(self.fan_row, self.fan_group, state.fan_speeds)
        self.state = state
        self._apply_state(state)
        self._set_controls_enabled(True)
        self.status("Up to date.")
        self.poll_timer.start()

    # ---------- commands ----------
    #
    # Changes show up in the window immediately and are sent in the background, so the
    # controls never lock up. A few seconds after the last command the window reads the
    # unit back to confirm; if LG rejects a command, that read-back corrects the display.

    def refresh(self) -> None:
        if self.controller is None or self.controller.device_id is None:
            return
        generation = self.generation
        self.runner.submit(
            self.controller.refresh(),
            lambda state: self._refreshed(state, generation),
            self._refresh_failed,
        )

    def _refreshed(self, state: AcState, generation: int) -> None:
        if generation != self.generation or self.in_flight:
            return  # the user changed something meanwhile; a newer read-back is coming
        self.state = state
        self._apply_state(state)
        if not self.error_shown:
            self.status("Up to date.")

    def _refresh_failed(self, error: BaseException) -> None:
        self._show_error(error)

    def _toggle_power(self) -> None:
        on = self.power_button.isChecked()
        self._send(self.controller.set_power(on), power=on)

    def _mode_clicked(self, button_id: int) -> None:
        mode = self.mode_group.button(button_id).property("value")
        if self.state and mode == self.state.mode:
            return
        self.pending_target = None
        self.temp_timer.stop()
        self._send(self.controller.set_mode(mode), mode=mode, can_set_temp=mode in self.state.temp_modes)

    def _fan_clicked(self, button_id: int) -> None:
        speed = self.fan_group.button(button_id).property("value")
        if self.state and speed == self.state.fan_speed:
            return
        self._send(self.controller.set_fan_speed(speed), fan_speed=speed)

    def _nudge_target(self, direction: int) -> None:
        state = self.state
        if state is None or state.target_temp is None:
            return
        value = (self.pending_target if self.pending_target is not None else state.target_temp) + direction * state.temp_step
        if state.temp_min is not None:
            value = max(value, state.temp_min)
        if state.temp_max is not None:
            value = min(value, state.temp_max)
        self.pending_target = value
        self.generation += 1
        self.target_label.setText(f"{value:g}°{state.unit}")
        self.temp_timer.start()  # wait for clicks to settle before sending one command

    def _send_target(self) -> None:
        value, self.pending_target = self.pending_target, None
        if value is None or self.state is None or value == self.state.target_temp:
            return
        self._send(self.controller.set_target_temperature(value, mode=self.state.mode), target_temp=value)

    def _send(self, coro, **changes) -> None:
        """Show `changes` right away, then send the command in the background."""
        self.generation += 1
        self.in_flight += 1
        self.error_shown = False
        self.settle_timer.stop()
        self.state = replace(self.state, **changes)
        self._apply_state(self.state)
        self.status("Sending…")
        self.runner.submit(coro, self._command_done, self._command_failed)

    def _command_done(self, _result) -> None:
        self.in_flight -= 1
        if self.in_flight == 0:
            if not self.error_shown:
                self.status("Sent.")
            self.settle_timer.start()

    def _command_failed(self, error: BaseException) -> None:
        self.in_flight -= 1
        self._show_error(error)
        if self.in_flight == 0:
            self.generation += 1
            self.refresh()  # put the controls back to what the unit really reports

    # ---------- display ----------

    def _apply_state(self, state: AcState) -> None:
        self.power_button.setChecked(bool(state.power))
        self.power_button.setText("On — click to turn off" if state.power else "Off — click to turn on")

        unit = f"°{state.unit}"
        self.current_label.setText(f"Room: {state.current_temp:g}{unit}" if state.current_temp is not None else "Room: —")
        if self.pending_target is None:
            self.target_label.setText(f"{state.target_temp:g}{unit}" if state.target_temp is not None else "—")
        has_target = state.can_set_temp and state.target_temp is not None and bool(state.power)
        self.down_button.setEnabled(has_target)
        self.up_button.setEnabled(has_target)

        self._check_value(self.mode_group, state.mode)
        self._check_value(self.fan_group, state.fan_speed)

    @staticmethod
    def _check_value(group: QButtonGroup, value: str | None) -> None:
        for button in group.buttons():
            button.setChecked(button.property("value") == value)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.power_button, self.down_button, self.up_button, self.refresh_button):
            widget.setEnabled(enabled)
        for group in (self.mode_group, self.fan_group):
            for button in group.buttons():
                button.setEnabled(enabled)
        if enabled and self.state:
            self._apply_state(self.state)  # re-applies which of -/+ make sense

    def _show_error(self, error: BaseException) -> None:
        if isinstance(error, ThinQAPIException):
            message = f"LG ThinQ said: {error.message} ({error.code})"
            hint = ERROR_HINTS.get(str(error.code))
            if hint:
                message += f" — {hint}"
        elif isinstance(error, aiohttp.ClientError):
            message = f"Network problem: {error}"
        else:
            message = str(error) or type(error).__name__
        self.error_shown = True
        self.status(message)

    def status(self, text: str) -> None:
        self.status_label.setText(text)

    def closeEvent(self, event) -> None:
        self.poll_timer.stop()
        if self.session:
            try:
                self.runner.run_sync(self.session.close())
            except Exception:
                pass
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LG AC Desktop")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    runner = AsyncRunner()
    window = MainWindow(runner, load_config(), demo="--demo" in app.arguments())
    window.show()
    QTimer.singleShot(0, window.start)
    code = app.exec()
    runner.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
