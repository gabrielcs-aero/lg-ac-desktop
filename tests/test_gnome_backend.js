// Runs the GNOME extension's backend under plain gjs against `lg-ac-cli --demo`.
//   gjs -m tests/test_gnome_backend.js
import GLib from 'gi://GLib';
import System from 'system';
import {AcBackend} from '../widgets/gnome/lg-ac@gabrielcs-aero.github.io/backend.js';

const repo = GLib.path_get_dirname(GLib.path_get_dirname(GLib.filename_from_uri(import.meta.url)[0]));
const cache = GLib.dir_make_tmp('lg-ac-test-XXXXXX');
// LG_AC_CLI overrides where lg-ac-cli lives (CI installs it on PATH).
const cliPath = GLib.getenv('LG_AC_CLI') || `${repo}/.venv/bin/lg-ac-cli`;
const cli = `env XDG_CACHE_HOME=${cache} ${cliPath} --demo`;

const loop = new GLib.MainLoop(null, false);
let failures = 0;
const check = (name, ok, detail = '') => {
    print(`${ok ? 'ok  ' : 'FAIL'} ${name}${ok ? '' : `  ${detail}`}`);
    if (!ok)
        failures++;
};
const after = (ms, fn) => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { fn(); return GLib.SOURCE_REMOVE; });

const backend = new AcBackend(cli, () => {});
backend.refresh();

after(3000, () => {
    check('loads state', backend.ready && backend.state.mode === 'COOL' && backend.state.target_temp === 22,
        JSON.stringify(backend.state));
    backend.setFan('HIGH');
    backend.setMode('AUTO');
    backend.nudge(1);
    backend.nudge(1);
    check('changes show immediately', backend.state.fan_speed === 'HIGH' && backend.state.mode === 'AUTO' &&
        backend.shownTarget === 23, `${backend.state.fan_speed} ${backend.state.mode} ${backend.shownTarget}`);
    check('status says sending', backend.message === 'Sending…', backend.message);
});

after(12000, () => {
    check('read-back confirms changes', backend.state.fan_speed === 'HIGH' && backend.state.mode === 'AUTO' &&
        backend.state.target_temp === 23, JSON.stringify(backend.state));
    check('status up to date', backend.message === 'Up to date', backend.message);
    backend.setFan('TURBO');
    check('rejected value shown optimistically', backend.state.fan_speed === 'TURBO');
});

after(17000, () => {
    check('error reported', backend.errorShown && backend.message.includes('TURBO'), backend.message);
    check('real state restored', backend.state.fan_speed === 'HIGH', backend.state.fan_speed);
    backend.setToggle('swing', 'swing', true);
    backend.setTimer('stop', 90);
    const eco = backend.state.presets.find(p => p.id === 'eco');
    backend.applyPreset(eco);
    check('extras show immediately', backend.state.swing === true && backend.state.timer_stop === 90 &&
        backend.state.power_save === true, JSON.stringify(backend.state));
    check('timer listed as active', backend.activeTimers.join() === 'stop', backend.activeTimers.join());
});

after(30000, () => {
    check('extras confirmed by read-back', backend.state.swing === true && backend.state.timer_stop === 90 &&
        backend.state.power_save === true && backend.state.target_temp === 26, JSON.stringify(backend.state));
    const eco = backend.state.presets.find(p => p.id === 'eco');
    check('eco preset now active', backend.presetActive(eco), JSON.stringify(eco.changes));
    backend.startWatching();
});

after(32000, () => {
    // Someone else changes the unit; the watch loop should pick it up without a refresh.
    GLib.spawn_command_line_async(`${cli} mode AUTO`);
});

after(38000, () => {
    check('live update arrives without a refresh', backend.state.mode === 'AUTO' && backend.live, `${backend.state.mode} live=${backend.live}`);
    // Quick cool to the lowest temperature: switches to cool and runs flat out.
    backend.setMode('COOL');
    for (let i = 0; i < 30; i++)
        backend.nudge(-1);
    backend.startBoost();
    check('quick cool shows immediately', backend.boosting && backend.shownTarget === 18 && backend.quick.label === 'Quick cool',
        JSON.stringify(backend.quick));
});

after(48000, () => {
    check('quick cool confirmed by read-back', backend.boosting && backend.state.fan_speed === 'HIGH' &&
        backend.state.target_temp === 18 && !backend.presetActive(backend.state.presets.find(p => p.id === 'max_cool')),
        JSON.stringify(backend.state));
    backend.stopBoost();
    check('stop shows immediately', !backend.boosting, JSON.stringify(backend.quick));
});

after(56000, () => {
    // The fan and power save go back to what Eco had set before the quick cool.
    check('stopped quick cool holds the target', !backend.boosting && backend.state.target_temp === 18 &&
        backend.state.fan_speed === 'LOW' && backend.state.power_save === true, JSON.stringify(backend.state));
    backend.destroy();
    loop.quit();
});

loop.run();
GLib.spawn_command_line_sync(`rm -rf ${cache}`);
print(failures ? `${failures} FAILED` : 'all passed');
if (failures)
    System.exit(1);
