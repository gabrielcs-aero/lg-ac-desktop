"""Desktop app: a QML window (qml/Main.qml) driven by AcModel."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Coroutine

import aiohttp
from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from thinqconnect import ThinQApi

from . import boost
from .boost import Boost, BoostService, BoostStore
from .config import Config, cache_dir, load_config, save_config
from .controller import AcController, AcState, DeviceInfo
from .demo import FileDemoApi
from .live import LiveUpdates, live_client_id
from .presets import get_preset, plan, presets_for
from .text import describe_error, pretty

HERE = Path(__file__).resolve().parent
POLL_INTERVAL_MS = 60_000
LIVE_POLL_INTERVAL_MS = 5 * 60_000  # safety net while LG pushes changes to us
TEMPERATURE_SEND_DELAY_MS = 700
# Changes made within this long of each other go out as one command: the unit beeps once per command.
BATCH_MS = 400
# How long after the last command to read the unit back, giving it time to apply the change.
SETTLE_MS = 4_000
DEMO_LATENCY_S = 1.0
TOKEN_URL = "https://connect-pat.lgthinq.com"


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
            if f.cancelled():  # only happens while shutting down
                return
            error = f.exception()
            callback = on_error if error else on_done
            self._finished.emit(callback, None if error else f.result(), error)

        future.add_done_callback(done)

    def run_sync(self, coro: Coroutine[Any, Any, Any], timeout: float = 5) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self) -> None:
        async def cancel_pending():
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            self.run_sync(cancel_pending(), timeout=2)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=2)

    @staticmethod
    def _deliver(callback, result, error) -> None:
        if callback:
            callback(error if error is not None else result)


class AcModel(QObject):
    """What the QML window shows and the actions it can take.

    Changes show up immediately and are sent in the background, one at a time; changes made in
    quick succession are grouped into one command, so the unit beeps once. A few seconds after
    the last command the unit is read back to confirm; if LG rejects a command, that read-back
    corrects the display.
    """

    changed = Signal()
    _liveState = Signal(object)  # AcState pushed by LG, delivered on the Qt thread
    _liveConnection = Signal(bool)

    def __init__(self, runner: AsyncRunner, config: Config, demo: bool = False):
        super().__init__()
        self.runner = runner
        self.config = config
        self.demo = demo
        self.session: aiohttp.ClientSession | None = None
        self.controller: AcController | None = None
        self.devices: list[DeviceInfo] = []
        self.device: DeviceInfo | None = None
        self.state: AcState | None = None
        self.boosting: Boost | None = None  # quick cool in progress (see boost.py)
        self.boost_service: BoostService | None = None
        self.pending_target: float | None = None
        self.in_flight = 0  # commands sent but not yet acknowledged
        self._batch: dict = {}  # changes waiting to go out together (see BATCH_MS)
        self.generation = 0  # bumped on every user change, so stale refreshes can be dropped
        self._message = ""
        self._error_shown = False
        self._needs_setup = False
        self.live: LiveUpdates | None = None
        self._live_connected = False
        self._liveState.connect(self._on_live_state)
        self._liveConnection.connect(self._on_live_connection)

        self.poll_timer = QTimer(self, interval=POLL_INTERVAL_MS, timeout=self.refresh)
        self.temp_timer = QTimer(self, singleShot=True, interval=TEMPERATURE_SEND_DELAY_MS, timeout=self._send_target)
        self.settle_timer = QTimer(self, singleShot=True, interval=SETTLE_MS, timeout=self.refresh)
        self.batch_timer = QTimer(self, singleShot=True, interval=BATCH_MS, timeout=self._flush)

    # ---------- properties for QML ----------

    @Property("QVariantMap", notify=changed)
    def ac(self) -> dict:
        if self.state is None:
            return {}
        # Tuples don't become JS arrays in QML, so hand over lists.
        data = {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self.state).items()}
        data["labels"] = {v: pretty(v) for v in [*self.state.modes, *self.state.fan_speeds]}
        data["boost"] = boost.describe(self.boosting, self.state)
        data["shown_target"] = self._shown_target()
        data["presets"] = presets_for(self.state)
        return data

    def _boost_active(self) -> bool:
        return self.state is not None and boost.describe(self.boosting, self.state)["active"]

    def _shown_target(self) -> float | None:
        if self.pending_target is not None:
            return self.pending_target
        # While boosting the unit is set to its coldest setting; show what we're aiming for.
        return self.boosting.target if self._boost_active() else self.state.target_temp

    @Property(bool, notify=changed)
    def ready(self) -> bool:
        return self.state is not None

    @Property(str, notify=changed)
    def alias(self) -> str:
        return self.device.alias if self.device else "Air conditioner"

    @Property(str, notify=changed)
    def message(self) -> str:
        return self._message

    @Property(bool, notify=changed)
    def errorShown(self) -> bool:
        return self._error_shown

    @Property(bool, notify=changed)
    def needsSetup(self) -> bool:
        return self._needs_setup

    @Property(bool, notify=changed)
    def isLive(self) -> bool:
        return self._live_connected

    @Property(bool, notify=changed)
    def isDemo(self) -> bool:
        return self.demo

    @Property(str, constant=True)
    def tokenUrl(self) -> str:
        return TOKEN_URL

    @Property(str, notify=changed)
    def countryCode(self) -> str:
        return self.config.country_code

    @Property(bool, notify=changed)
    def hasToken(self) -> bool:
        return bool(self.config.access_token)

    def _set_message(self, text: str, error: bool = False) -> None:
        self._message = text
        self._error_shown = error
        self.changed.emit()

    # ---------- setup and connection ----------

    def start(self) -> None:
        if not self.demo and not self.config.is_complete:
            self._needs_setup = True
            self.changed.emit()
            return
        self.connect_api()

    @Slot()
    def openSetup(self) -> None:
        self._needs_setup = True
        self.changed.emit()

    @Slot()
    def cancelSetup(self) -> None:
        if self.demo or self.config.is_complete:
            self._needs_setup = False
            self.changed.emit()

    @Slot(str, str, result=str)
    def saveSetup(self, token: str, country: str) -> str:
        """Returns an error message, or "" when saved."""
        token, country = token.strip(), country.strip().upper()
        if not token and self.config.access_token:
            token = self.config.access_token  # left blank: keep the saved token
        if not token or len(country) != 2 or not country.isalpha():
            return "Enter the token and a two-letter country code."
        self.config.access_token, self.config.country_code = token, country
        save_config(self.config)
        self.demo = False
        self._needs_setup = False
        self.connect_api()
        return ""

    def connect_api(self) -> None:
        self.poll_timer.stop()
        self._live_connected = False
        self.state = None
        self._set_message("Connecting to LG ThinQ…")
        self.runner.submit(self._open_api(), self._devices_loaded, self._show_error)

    async def _open_api(self) -> list[DeviceInfo]:
        if self.demo:
            # Shared with `lg-ac-cli --demo`, so the demo app and demo widgets show one unit.
            self.controller = AcController(FileDemoApi(cache_dir() / "demo-status.json", latency=DEMO_LATENCY_S))
            self._make_boost_service("demo-boost.json")
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
        self._make_boost_service("boost.json")
        return await self.controller.list_air_conditioners()

    def _make_boost_service(self, filename: str) -> None:
        demo = self.demo
        self.boost_service = BoostService(
            self.controller, BoostStore(cache_dir() / filename),
            spawn=lambda run_id: boost.spawn_runner(run_id, demo=demo),
        )

    def _set_state(self, state: AcState) -> None:
        """Take a state read from the unit, along with the quick cool status saved next to it."""
        before = self.boosting if self._boost_active() else None
        self.state = state
        self.boosting = self.boost_service.current() if self.boost_service else None
        if before and not self._boost_active() and not self._error_shown:
            self._message = f"{boost.label(before.mode)} done, holding {before.target:g}°"

    def _devices_loaded(self, devices: list[DeviceInfo]) -> None:
        self.devices = devices
        if not devices:
            self._set_message("No air conditioner found on this LG account. Is it set up in the ThinQ app?", True)
            return
        device = next((d for d in devices if d.device_id == self.config.device_id), devices[0])
        if not self.demo and self.config.device_id != device.device_id:
            self.config.device_id = device.device_id
            save_config(self.config)
        self.device = device
        self._set_message(f"Loading {device.alias}…")
        self.runner.submit(self.controller.connect(device), self._connected, self._show_error)

    def _connected(self, state: AcState) -> None:
        self._set_state(state)
        self._set_message("Up to date")
        self.poll_timer.start()
        if not self.demo:
            self.runner.submit(self._start_live(), None, lambda error: None)

    # ---------- live updates ----------

    async def _start_live(self) -> None:
        if self.live is not None:
            await self.live.stop()
        controller = self.controller
        self.live = LiveUpdates(
            self.session,
            self.config.access_token,
            self.config.country_code,
            live_client_id(self.config.client_id, "app"),
            self.device.device_id,
            # Runs on the asyncio loop, like every other controller call.
            on_report=lambda report: self._liveState.emit(controller.apply_report(report)),
            store_dir=cache_dir() / "mqtt-app",
            on_connection_change=self._liveConnection.emit,
        )
        self._liveConnection.emit(await self.live.start() and self.live.connected)

    def _on_live_state(self, state: AcState) -> None:
        if self.in_flight or self._batch or self.pending_target is not None:
            return  # a change of ours is on its way; the read-back after it will catch up
        self._update(state)

    def _on_live_connection(self, connected: bool) -> None:
        if connected == self._live_connected:
            return
        self._live_connected = connected
        self.poll_timer.setInterval(LIVE_POLL_INTERVAL_MS if connected else POLL_INTERVAL_MS)
        self.changed.emit()

    # ---------- commands ----------

    @Slot()
    def refresh(self) -> None:
        if self.controller is None or self.controller.device_id is None:
            return
        generation = self.generation
        self.runner.submit(
            self.controller.refresh(),
            lambda state: self._refreshed(state, generation),
            self._show_error,
        )

    def _refreshed(self, state: AcState, generation: int) -> None:
        if generation != self.generation or self.in_flight or self._batch:
            return  # the user changed something meanwhile; a newer read-back is coming
        self._update(state)

    def _update(self, state: AcState) -> None:
        message = self._message
        self._set_state(state)
        if self._error_shown or self._message != message:  # keep an error or "done" note visible
            self.changed.emit()
        else:
            self._set_message("Up to date")

    @Slot(bool)
    def setPower(self, on: bool) -> None:
        if not self.state or on == self.state.power:
            return
        if not on and self._boost_active():
            # Hold the target from now on, so turning the unit back on doesn't resume full blast.
            changes = boost.finish_changes(self.boosting, self.state)
            self.boosting = None
            self._send(self.boost_service.stop(also={"power": False}), power=False, **changes)
            return
        self._queue(power=on)

    @Slot()
    def startBoost(self) -> None:
        """Quick cool (or heat) to the target shown on the dial."""
        if not self.state or self.boost_service is None or self._boost_active():
            return
        target = self._shown_target()
        if target is None:
            return
        try:
            planned, changes = boost.plan_start(self.state, target, time.time())
        except ValueError as error:
            self._set_message(str(error), True)
            return
        self.pending_target = None
        self.temp_timer.stop()
        self.boosting = planned
        if planned is None and not changes:
            self._set_message(f"The room is already at {target:g}°")
            return
        self._send(self.boost_service.start(target), **changes)

    @Slot()
    def stopBoost(self) -> None:
        """Stop quick cool and hold the target from now on."""
        if not self._boost_active():
            return
        changes = boost.finish_changes(self.boosting, self.state)
        self.boosting = None
        self._send(self.boost_service.stop(), **changes)

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if not self.state or mode == self.state.mode:
            return
        self.pending_target = None
        self.temp_timer.stop()
        self._queue(mode=mode, can_set_temp=mode in self.state.temp_modes)

    @Slot(str)
    def setFanSpeed(self, speed: str) -> None:
        if self.state and speed != self.state.fan_speed:
            self._queue(fan_speed=speed)

    @Slot(bool)
    def setSwing(self, on: bool) -> None:
        if self.state and self.state.can_swing and on != self.state.swing:
            self._queue(swing=on)

    @Slot(bool)
    def setPowerSave(self, on: bool) -> None:
        if self.state and self.state.can_power_save and on != self.state.power_save:
            self._queue(power_save=on)

    @Slot(bool)
    def setDisplayLight(self, on: bool) -> None:
        if self.state and self.state.can_display_light and on != self.state.display_light:
            self._queue(display_light=on)

    @Slot(str, int)
    def setTimer(self, kind: str, minutes: int) -> None:
        """Start a countdown of `minutes` for kind start/stop/sleep; 0 cancels it."""
        if not self.state or kind not in self.state.timers:
            return
        self._queue(**{f"timer_{kind}": minutes or None})

    @Slot(str)
    def applyPreset(self, preset_id: str) -> None:
        if not self.state:
            return
        changes = plan(get_preset(preset_id), self.state)
        if not changes:
            self._set_message(f"Already on {get_preset(preset_id).name}")
            return
        self.pending_target = None
        self.temp_timer.stop()
        self._send(self.controller.apply(changes), **changes)

    @Slot(int)
    def nudge(self, direction: int) -> None:
        state = self.state
        if state is None or state.target_temp is None or not state.can_set_temp or not state.power:
            return
        value = self._shown_target() + direction * state.temp_step
        if state.temp_min is not None:
            value = max(value, state.temp_min)
        if state.temp_max is not None:
            value = min(value, state.temp_max)
        self.pending_target = value
        self.generation += 1
        self.batch_timer.stop()  # other pending changes go out together with the temperature
        self.temp_timer.start()  # wait for clicks to settle before sending one command
        self.changed.emit()

    def _send_target(self) -> None:
        value, self.pending_target = self.pending_target, None
        if value is None or self.state is None or (not self._boost_active() and value == self.state.target_temp):
            self._flush()
            self.changed.emit()
            return
        if self._boost_active():  # aim the quick cool somewhere else; the unit stays on full blast
            if value != self.boosting.target:
                self.boosting = replace(self.boosting, target=value)
                self._send(self.boost_service.start(value))
            else:
                self._flush()
                self.changed.emit()
            return
        self._queue(target_temp=value)
        self._flush()  # the −/+ clicks have already settled

    def _queue(self, **changes) -> None:
        """Show `changes` right away; send them shortly, together with any made meanwhile."""
        self.generation += 1
        self.settle_timer.stop()
        self.state = replace(self.state, **changes)
        self._batch.update(changes)
        self.batch_timer.start()
        self._set_message("Sending…")

    def _flush(self) -> None:
        self.batch_timer.stop()
        changes, self._batch = self._batch, {}
        if changes:
            self._submit(self.controller.apply(changes, mode=self.state.mode))

    def _send(self, coro, **changes) -> None:
        """Show `changes` right away, then send `coro` (a command of its own) in the background."""
        self._flush()
        self.generation += 1
        self.settle_timer.stop()
        self.state = replace(self.state, **changes)
        self._set_message("Sending…")
        self._submit(coro)

    def _submit(self, coro) -> None:
        self.in_flight += 1
        self.runner.submit(coro, self._command_done, self._command_failed)

    def _command_done(self, _result) -> None:
        self.in_flight -= 1
        if self.in_flight == 0:
            if not self._error_shown:
                self._set_message("Sent")
            self.settle_timer.start()

    def _command_failed(self, error: BaseException) -> None:
        self.in_flight -= 1
        self._show_error(error)
        if self.in_flight == 0:
            self.generation += 1
            self.refresh()  # put the controls back to what the unit really reports

    def _show_error(self, error: BaseException) -> None:
        self._set_message(describe_error(error), True)

    def close(self) -> None:
        self.poll_timer.stop()
        if self._batch:  # don't drop a change made just before closing
            try:
                self.runner.run_sync(self.controller.apply(self._batch, mode=self.state.mode))
            except Exception:
                pass
        if self.live:
            try:
                self.runner.run_sync(self.live.stop(), timeout=6)
            except Exception:
                pass
        if self.session:
            try:
                self.runner.run_sync(self.session.close())
            except Exception:
                pass


def create_engine(model: AcModel) -> QQmlApplicationEngine:
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("acModel", model)
    engine.rootContext().setContextProperty("iconDir", QUrl.fromLocalFile(str(HERE / "icons") + "/"))
    engine.rootContext().setContextProperty("themeOverride", os.environ.get("LG_AC_THEME", ""))
    engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
    return engine


def main() -> int:
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("LG AC Desktop")
    app.setDesktopFileName("lg-ac-desktop")
    app.setWindowIcon(QIcon(str(HERE / "icons" / "lg-ac.svg")))
    runner = AsyncRunner()
    model = AcModel(runner, load_config(), demo="--demo" in app.arguments())
    engine = create_engine(model)
    if not engine.rootObjects():
        return 1
    QTimer.singleShot(0, model.start)
    code = app.exec()
    model.close()
    del engine  # tear down the window before the model it reads from
    runner.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
