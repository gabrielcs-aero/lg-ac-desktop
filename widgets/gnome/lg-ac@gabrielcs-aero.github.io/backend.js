// Talks to the air conditioner through this project's `lg-ac-cli` command.
//
// Changes are applied to `state` immediately and sent one at a time in the background.
// A few seconds after the last command the unit is read back to confirm; reads that
// started before a newer change are dropped so they can't undo it.
//
// Uses only GLib/Gio, so it also runs under plain `gjs` (see tests/test_gnome_backend.js).

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async');

const SAFE_ARG = /^[A-Za-z0-9_.-]+$/;
const SETTLE_MS = 4000;
const TEMP_DELAY_MS = 700;

export class AcBackend {
    /**
     * @param {string} commandLine e.g. "/home/me/lg-ac-desktop/.venv/bin/lg-ac-cli"
     * @param {() => void} onChange called whenever state or message changes
     */
    constructor(commandLine, onChange) {
        this.argv = GLib.shell_parse_argv(commandLine)[1];
        this.onChange = onChange;
        this.state = null;
        this.message = 'Loading…';
        this.errorShown = false;
        this.pendingTarget = null;
        this._generation = 0;
        this._pending = 0;
        this._queue = [];
        this._commandRunning = false;
        this._statusRunning = false;
        this._timers = new Map();
        this._cancellable = new Gio.Cancellable();
    }

    destroy() {
        this._cancellable.cancel();
        for (const id of this._timers.values())
            GLib.source_remove(id);
        this._timers.clear();
        this.onChange = null;
    }

    get ready() { return this.state !== null; }
    get isOn() { return this.ready && this.state.power === true; }
    get shownTarget() { return this.pendingTarget ?? this.state?.target_temp ?? null; }
    get canSetTemp() { return this.isOn && this.state.can_set_temp && this.shownTarget !== null; }
    label(value) { return this.state?.labels?.[value] ?? value; }

    // ---------- commands ----------

    refresh() {
        if (this._statusRunning)
            return;
        this._statusRunning = true;
        const generation = this._generation;
        this._run(['status']).then(result => {
            this._statusRunning = false;
            if (!result.ok) {
                this._showError(result.error);
            } else if (generation === this._generation && this._pending === 0) {
                this.state = result.state;
                if (!this.errorShown)
                    this.message = 'Up to date';
            }
            this._changed();
        });
    }

    setPower(on) {
        this._send('power', ['power', on ? 'on' : 'off'], {power: on});
    }

    setMode(mode) {
        if (!this.ready || mode === this.state.mode)
            return;
        this.pendingTarget = null;
        this._clearTimer('temp');
        this._send('mode', ['mode', mode], {mode, can_set_temp: this.state.temp_modes.includes(mode)});
    }

    setFan(speed) {
        if (this.ready && speed !== this.state.fan_speed)
            this._send('fan', ['fan', speed], {fan_speed: speed});
    }

    nudge(direction) {
        if (!this.canSetTemp)
            return;
        const {temp_step: step, temp_min: min, temp_max: max} = this.state;
        let value = this.shownTarget + direction * step;
        if (min !== null)
            value = Math.max(value, min);
        if (max !== null)
            value = Math.min(value, max);
        this._generation++;
        this.pendingTarget = value;
        this._setTimer('temp', TEMP_DELAY_MS, () => this._sendTarget());
        this._changed();
    }

    // ---------- internals ----------

    _sendTarget() {
        const value = this.pendingTarget;
        this.pendingTarget = null;
        if (value === null || !this.ready || value === this.state.target_temp)
            return;
        this._send('temp', ['temp', String(value), '--mode', this.state.mode], {target_temp: value});
    }

    _send(kind, args, changes) {
        this._generation++;
        this.errorShown = false;
        this._clearTimer('settle');
        if (this.state)
            this.state = {...this.state, ...changes};
        // A newer click of the same kind replaces one that hasn't been sent yet.
        const queued = this._queue.find(c => c.kind === kind);
        if (queued) {
            queued.args = args;
        } else {
            this._queue.push({kind, args});
            this._pending++;
        }
        this.message = 'Sending…';
        this._changed();
        this._pump();
    }

    _pump() {
        if (this._commandRunning || this._queue.length === 0)
            return;
        const {args} = this._queue.shift();
        this._commandRunning = true;
        this._run(args).then(result => {
            this._commandRunning = false;
            this._pending--;
            if (!result.ok) {
                this._showError(result.error);
                if (this._pending === 0) {
                    this._generation++;
                    this.refresh(); // restore what the unit really has
                }
            } else if (this._pending === 0) {
                if (!this.errorShown)
                    this.message = 'Sent';
                this._setTimer('settle', SETTLE_MS, () => this.refresh());
            }
            this._changed();
            this._pump();
        });
    }

    async _run(args) {
        if (!args.every(a => SAFE_ARG.test(a)))
            return {ok: false, error: 'Invalid value'};
        try {
            const proc = Gio.Subprocess.new([...this.argv, ...args],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
            const [stdout, stderr] = await proc.communicate_utf8_async(null, this._cancellable);
            try {
                return JSON.parse(stdout);
            } catch {
                return {ok: false, error: stderr.trim() || 'lg-ac-cli gave no answer'};
            }
        } catch (e) {
            if (e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                return {ok: false, error: 'Cancelled'};
            return {ok: false, error: `Can't run lg-ac-cli: ${e.message}`};
        }
    }

    _showError(text) {
        this.errorShown = true;
        this.message = text;
    }

    _changed() {
        this.onChange?.();
    }

    _setTimer(name, ms, callback) {
        this._clearTimer(name);
        this._timers.set(name, GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._timers.delete(name);
            callback();
            return GLib.SOURCE_REMOVE;
        }));
    }

    _clearTimer(name) {
        const id = this._timers.get(name);
        if (id) {
            GLib.source_remove(id);
            this._timers.delete(name);
        }
    }
}
