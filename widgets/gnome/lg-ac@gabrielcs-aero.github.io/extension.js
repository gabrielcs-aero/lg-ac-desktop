// LG Air Conditioner — GNOME Shell top-bar indicator.
//
// Shows the mode and target temperature in the top bar; the menu has power, a temperature
// dial, mode buttons and a fan-speed slider. All device access goes through backend.js.

import Cairo from 'cairo';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

import {AcBackend} from './backend.js';

const MODE_ICONS = {COOL: 'mode-cool', HEAT: 'mode-heat', AIR_DRY: 'mode-dry', FAN: 'mode-fan', AUTO: 'mode-auto'};
const MODE_COLORS = {COOL: '#3b82f6', HEAT: '#f97316', AIR_DRY: '#06b6d4', FAN: '#8b5cf6', AUTO: '#10b981'};
const DEFAULT_COLOR = '#3b82f6';
const PRESET_TINTS = {sleep: '#8b5cf6', max_cool: '#3b82f6', eco: '#10b981'};
const TOGGLES = [
    {command: 'swing', field: 'swing', can: 'can_swing', icon: 'swing', label: 'Swing', tint: '#0ea5e9'},
    {command: 'power-save', field: 'power_save', can: 'can_power_save', icon: 'power-save', label: 'Power save', tint: '#10b981'},
    {command: 'display', field: 'display_light', can: 'can_display_light', icon: 'display', label: 'Display', tint: '#f59e0b'},
];
const TIMER_VERBS = {stop: 'Off in', start: 'On in', sleep: 'Sleep, off in'};
const DURATIONS = [30, 60, 120, 240, 360, 480];

function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function duration(minutes) {
    const h = Math.floor(minutes / 60), m = minutes % 60;
    return h && m ? `${h} h ${m} min` : h ? `${h} h` : `${m} min`;
}

function formatTemp(t) {
    return t === null || t === undefined ? '–' : String(Math.round(t * 2) / 2);
}

const Indicator = GObject.registerClass(
class LgAcIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.5, 'LG Air Conditioner');
        this._extension = extension;
        this._settings = extension.getSettings();
        this._syncing = false;
        this._modeButtons = new Map();
        this._presetButtons = new Map();
        this._toggleButtons = new Map();
        this._timerMenuKey = null;

        this._backend = new AcBackend(this._settings.get_string('command'), () => this._sync());

        // Top bar: mode icon + target temperature
        const box = new St.BoxLayout({style_class: 'panel-status-menu-box lg-ac-panel'});
        this._panelIcon = new St.Icon({style_class: 'system-status-icon', gicon: this._icon('mode-cool')});
        this._panelLabel = new St.Label({text: '–', y_align: Clutter.ActorAlign.CENTER});
        box.add_child(this._panelIcon);
        box.add_child(this._panelLabel);
        this.add_child(box);

        this._buildMenu();
        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this._backend.refresh();
        });

        const poll = Math.max(15, this._settings.get_int('poll-seconds'));
        let ticks = 0;
        this._pollId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, poll, () => {
            // While LG pushes changes to us, polling is only a safety net: every 5th tick.
            if (!this._backend.live || ++ticks % 5 === 0)
                this._backend.refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._backend.refresh();
        this._backend.startWatching();
        this._sync();
    }

    _icon(name) {
        // Files ending in -symbolic.svg are recoloured by the shell theme / CSS `color`.
        return Gio.icon_new_for_string(`${this._extension.path}/icons/${name}-symbolic.svg`);
    }

    _buildMenu() {
        // Header: name, room temperature and power switch
        this._power = new PopupMenu.PopupSwitchMenuItem('Air conditioner', false);
        this._power.label.add_style_class_name('lg-ac-title');
        this._power.connect('toggled', (_item, state) => this._backend.setPower(state));
        this.menu.addMenuItem(this._power);

        this._room = new PopupMenu.PopupMenuItem('', {reactive: false, style_class: 'lg-ac-room'});
        this.menu.addMenuItem(this._room);

        this._fault = new PopupMenu.PopupMenuItem('The unit reports a fault. Check it, or look in the ThinQ app.',
            {reactive: false, style_class: 'lg-ac-fault'});
        this._fault.label.clutter_text.line_wrap = true;
        this.menu.addMenuItem(this._fault);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Temperature dial flanked by −/+
        const tempItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const tempRow = new St.BoxLayout({x_expand: true, x_align: Clutter.ActorAlign.CENTER, style_class: 'lg-ac-temp-row'});
        this._minus = this._stepButton('−', -1);
        this._plus = this._stepButton('+', 1);

        const dialStack = new St.Widget({layout_manager: new Clutter.BinLayout()});
        this._dial = new St.DrawingArea({style_class: 'lg-ac-dial', width: 150, height: 150});
        this._dial.connect('repaint', area => this._paintDial(area));
        const dialText = new St.BoxLayout({vertical: true, x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
        this._targetLabel = new St.Label({style_class: 'lg-ac-target', x_align: Clutter.ActorAlign.CENTER});
        this._targetCaption = new St.Label({style_class: 'lg-ac-caption', x_align: Clutter.ActorAlign.CENTER});
        dialText.add_child(this._targetLabel);
        dialText.add_child(this._targetCaption);
        dialStack.add_child(this._dial);
        dialStack.add_child(dialText);

        tempRow.add_child(this._minus);
        tempRow.add_child(dialStack);
        tempRow.add_child(this._plus);
        tempItem.add_child(tempRow);
        this.menu.addMenuItem(tempItem);

        // Quick cool: full power until the room reaches the target, then hold it
        this._quickItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const quickContent = new St.BoxLayout({style_class: 'lg-ac-quick-content'});
        this._quickIcon = new St.Icon({gicon: this._icon('quick-cool'), icon_size: 16});
        this._quickLabel = new St.Label({y_align: Clutter.ActorAlign.CENTER});
        quickContent.add_child(this._quickIcon);
        quickContent.add_child(this._quickLabel);
        this._quick = new St.Button({child: quickContent, style_class: 'lg-ac-quick', x_expand: true, x_align: Clutter.ActorAlign.CENTER});
        this._quick.connect('clicked', () => {
            if (this._backend.boosting)
                this._backend.stopBoost();
            else
                this._backend.startBoost();
        });
        this._quickItem.add_child(this._quick);
        this.menu.addMenuItem(this._quickItem);

        // Presets (filled in once known)
        const presetItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        this._presetRow = new St.BoxLayout({x_expand: true, style_class: 'lg-ac-preset-row'});
        presetItem.add_child(this._presetRow);
        this.menu.addMenuItem(presetItem);

        // Mode buttons (filled in once the modes are known)
        const modeItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        this._modeRow = new St.BoxLayout({x_expand: true, style_class: 'lg-ac-mode-row'});
        modeItem.add_child(this._modeRow);
        this.menu.addMenuItem(modeItem);

        // Fan speed slider
        const fanHeader = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        fanHeader.add_child(new St.Icon({gicon: this._icon('mode-fan'), style_class: 'popup-menu-icon'}));
        fanHeader.add_child(new St.Label({text: 'Fan', x_expand: true, y_align: Clutter.ActorAlign.CENTER}));
        this._fanLabel = new St.Label({style_class: 'lg-ac-fan-value', y_align: Clutter.ActorAlign.CENTER});
        fanHeader.add_child(this._fanLabel);
        this.menu.addMenuItem(fanHeader);

        const fanItem = new PopupMenu.PopupBaseMenuItem({activate: false});
        this._fanSlider = new Slider(0);
        this._fanSlider.x_expand = true;
        this._fanSlider.connect('notify::value', () => this._fanMoved());
        fanItem.add_child(this._fanSlider);
        this.menu.addMenuItem(fanItem);

        // Swing, power save, display
        const toggleItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const toggleRow = new St.BoxLayout({x_expand: true, style_class: 'lg-ac-toggle-row'});
        for (const toggle of TOGGLES) {
            const button = this._roundToggle(toggle);
            this._toggleButtons.set(toggle.field, button);
            toggleRow.add_child(button);
        }
        toggleItem.add_child(toggleRow);
        this.menu.addMenuItem(toggleItem);

        // Timer
        this._timerMenu = new PopupMenu.PopupSubMenuMenuItem('Timer', true);
        this._timerMenu.icon.gicon = this._icon('timer');
        this.menu.addMenuItem(this._timerMenu);

        // Status line + refresh
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const statusItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        this._status = new St.Label({style_class: 'lg-ac-status', x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        const refresh = new St.Button({
            style_class: 'lg-ac-icon-button',
            child: new St.Icon({icon_name: 'view-refresh-symbolic', icon_size: 16}),
            accessible_name: 'Refresh',
        });
        refresh.connect('clicked', () => this._backend.refresh());
        statusItem.add_child(this._status);
        statusItem.add_child(refresh);
        this.menu.addMenuItem(statusItem);

        this._filter = new PopupMenu.PopupImageMenuItem('', this._icon('filter'), {reactive: false, style_class: 'lg-ac-filter'});
        this.menu.addMenuItem(this._filter);

        const openApp = new PopupMenu.PopupMenuItem('Open LG AC app');
        openApp.connect('activate', () => this._openApp());
        this.menu.addMenuItem(openApp);
    }

    _stepButton(text, direction) {
        const button = new St.Button({
            label: text,
            style_class: 'lg-ac-step',
            y_align: Clutter.ActorAlign.CENTER,
            accessible_name: direction < 0 ? 'Lower temperature' : 'Raise temperature',
        });
        button.connect('clicked', () => this._backend.nudge(direction));
        return button;
    }

    _modeButton(mode) {
        const content = new St.BoxLayout({vertical: true, x_align: Clutter.ActorAlign.CENTER});
        const icon = new St.Icon({gicon: this._icon(MODE_ICONS[mode] ?? 'mode-auto'), icon_size: 20});
        const label = new St.Label({text: this._backend.label(mode), style_class: 'lg-ac-mode-label'});
        content.add_child(icon);
        content.add_child(label);
        const button = new St.Button({
            child: content,
            style_class: 'lg-ac-mode',
            x_expand: true,
            toggle_mode: false,
            accessible_name: this._backend.label(mode),
        });
        button.connect('clicked', () => this._backend.setMode(mode));
        return button;
    }

    _presetButton(preset) {
        const content = new St.BoxLayout({x_align: Clutter.ActorAlign.CENTER, style_class: 'lg-ac-preset-content'});
        const icon = new St.Icon({gicon: this._icon(preset.icon), icon_size: 14});
        content.add_child(icon);
        content.add_child(new St.Label({text: preset.name, y_align: Clutter.ActorAlign.CENTER}));
        const button = new St.Button({child: content, style_class: 'lg-ac-preset', x_expand: true, accessible_name: preset.name});
        button._icon = icon;
        button._tint = PRESET_TINTS[preset.id] ?? DEFAULT_COLOR;
        button.connect('clicked', () => {
            const current = this._backend.state?.presets?.find(p => p.id === preset.id);
            if (current)
                this._backend.applyPreset(current);
        });
        return button;
    }

    _roundToggle(toggle) {
        const box = new St.BoxLayout({vertical: true, x_align: Clutter.ActorAlign.CENTER, style_class: 'lg-ac-toggle-content'});
        const circle = new St.Bin({style_class: 'lg-ac-toggle-circle', x_align: Clutter.ActorAlign.CENTER,
            child: new St.Icon({gicon: this._icon(toggle.icon), icon_size: 18})});
        box.add_child(circle);
        box.add_child(new St.Label({text: toggle.label, style_class: 'lg-ac-toggle-label', x_align: Clutter.ActorAlign.CENTER}));
        const button = new St.Button({child: box, style_class: 'lg-ac-toggle', x_expand: true, accessible_name: toggle.label});
        button._circle = circle;
        button.connect('clicked', () => {
            const s = this._backend.state;
            if (s)
                this._backend.setToggle(toggle.command, toggle.field, s[toggle.field] !== true);
        });
        return button;
    }

    _rebuildTimerMenu() {
        const b = this._backend;
        const s = b.state;
        const kind = b.isOn ? 'stop' : 'start';
        const active = b.activeTimers.map(k => `${k}:${s[`timer_${k}`]}`).join();
        const key = `${kind}|${active}|${s.timers.join()}`;
        if (key === this._timerMenuKey)
            return;
        this._timerMenuKey = key;
        this._timerMenu.menu.removeAll();

        for (const k of b.activeTimers) {
            const minutes = s[`timer_${k}`];
            const item = new PopupMenu.PopupMenuItem(`${TIMER_VERBS[k]}${minutes > 0 ? ` ${duration(minutes)}` : ''}  ·  Cancel`);
            item.connect('activate', () => b.setTimer(k, 0));
            this._timerMenu.menu.addMenuItem(item);
        }
        if (b.activeTimers.length)
            this._timerMenu.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        if (s.timers.includes(kind)) {
            for (const minutes of DURATIONS) {
                const item = new PopupMenu.PopupMenuItem(`${kind === 'stop' ? 'Turn off' : 'Turn on'} in ${duration(minutes)}`);
                item.connect('activate', () => b.setTimer(kind, minutes));
                this._timerMenu.menu.addMenuItem(item);
            }
        }
    }

    _paintDial(area) {
        const b = this._backend;
        const cr = area.get_context();
        const [w, h] = area.get_surface_size();
        const fg = area.get_theme_node().get_foreground_color();
        const line = 10;
        const r = Math.min(w, h) / 2 - line;
        const start = Math.PI * 0.75, sweep = Math.PI * 1.5;

        let fraction = 0;
        if (b.ready && b.shownTarget !== null && b.state.temp_min !== null)
            fraction = (b.shownTarget - b.state.temp_min) / (b.state.temp_max - b.state.temp_min);

        cr.setLineCap(Cairo.LineCap.ROUND);
        cr.setLineWidth(line);
        cr.setSourceRGBA(fg.red / 255, fg.green / 255, fg.blue / 255, 0.15);
        cr.arc(w / 2, h / 2, r, start, start + sweep);
        cr.stroke();

        const [red, green, blue] = b.isOn ? hexToRgb(MODE_COLORS[b.state.mode] ?? DEFAULT_COLOR) : [0.5, 0.5, 0.5];
        cr.setSourceRGBA(red, green, blue, 1);
        cr.arc(w / 2, h / 2, r, start, start + sweep * Math.max(0.01, Math.min(1, fraction)));
        cr.stroke();
        cr.$dispose();
    }

    _fanMoved() {
        if (this._syncing || !this._backend.ready)
            return;
        const speeds = this._backend.state.fan_speeds;
        const index = Math.round(this._fanSlider.value * (speeds.length - 1));
        this._fanLabel.text = this._backend.label(speeds[index]);
        // Send once the slider settles.
        if (this._fanTimer)
            GLib.source_remove(this._fanTimer);
        this._fanTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
            this._fanTimer = 0;
            this._backend.setFan(speeds[index]);
            return GLib.SOURCE_REMOVE;
        });
    }

    _openApp() {
        try {
            const argv = GLib.shell_parse_argv(this._settings.get_string('app-command'))[1];
            Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
        } catch (e) {
            Main.notifyError('LG Air Conditioner', `Couldn't open the app: ${e.message}`);
        }
    }

    _sync() {
        const b = this._backend;
        this._syncing = true;
        const s = b.state;
        const color = b.isOn ? MODE_COLORS[s.mode] ?? DEFAULT_COLOR : null;

        // Top bar
        this._panelIcon.gicon = this._icon(s ? MODE_ICONS[s.mode] ?? 'mode-auto' : 'mode-cool');
        this._panelIcon.style = color ? `color: ${color};` : '';
        this._panelLabel.text = !s ? '–' : b.isOn ? `${formatTemp(b.shownTarget)}°` : 'Off';
        this._panelLabel.opacity = b.isOn || !s ? 255 : 150;

        // Menu
        this._power.label.text = s?.alias ?? 'Air conditioner';
        this._power.setToggleState(b.isOn);
        this._power.setSensitive(b.ready);
        this._room.label.text = s ? `Room ${formatTemp(s.current_temp)}°${s.unit}${b.live ? '  ·  ● Live' : ''}` : '';

        this._targetLabel.text = s ? `${formatTemp(b.shownTarget)}°${s.unit}` : '–';
        this._targetLabel.opacity = b.isOn ? 255 : 128;
        this._targetCaption.text = !s ? '' : !b.isOn ? 'Off' : b.boosting ? `${b.quick.label} · then holds`
            : s.can_set_temp ? 'Target' : `No target in ${b.label(s.mode)}`;
        this._minus.reactive = this._plus.reactive = b.canSetTemp;
        this._minus.opacity = this._plus.opacity = b.canSetTemp ? 255 : 90;
        this._dial.queue_repaint();

        const quickColor = b.quickHeat ? MODE_COLORS.HEAT : MODE_COLORS.COOL;
        this._quickItem.visible = Boolean(s) && b.quick.available && b.shownTarget !== null;
        if (this._quickItem.visible) {
            this._quickIcon.gicon = this._icon(b.quickHeat ? 'quick-heat' : 'quick-cool');
            this._quickLabel.text = b.boosting
                ? `${b.quickHeat ? 'Quick heating' : 'Quick cooling'} · ${formatTemp(s.current_temp)}° → ${formatTemp(b.quick.target)}°   |   Stop`
                : `${b.quick.label} to ${formatTemp(b.shownTarget)}°`;
            this._quick.style = b.boosting ? `background-color: ${quickColor}; color: white;` : '';
            this._quickIcon.style = b.boosting ? 'color: white;' : `color: ${quickColor};`;
            this._quick.reactive = !b.quickAlreadyThere;
            this._quick.opacity = b.quickAlreadyThere ? 115 : 255;
            this._quick.accessible_name = b.boosting ? `Stop ${b.quick.label.toLowerCase()}` : b.quick.label;
        }

        if (s) {
            if ([...this._modeButtons.keys()].join() !== s.modes.join()) {
                this._modeRow.destroy_all_children();
                this._modeButtons.clear();
                for (const mode of s.modes) {
                    const button = this._modeButton(mode);
                    this._modeButtons.set(mode, button);
                    this._modeRow.add_child(button);
                }
            }
            for (const [mode, button] of this._modeButtons) {
                const selected = mode === s.mode;
                button.reactive = b.isOn;
                button.opacity = b.isOn ? 255 : 128;
                button.style = selected ? `background-color: ${MODE_COLORS[mode] ?? DEFAULT_COLOR}; color: white;` : '';
                if (selected)
                    button.add_style_pseudo_class('checked');
                else
                    button.remove_style_pseudo_class('checked');
            }

            const speeds = s.fan_speeds;
            const index = Math.max(0, speeds.indexOf(s.fan_speed));
            if (!this._fanTimer) {
                this._fanSlider.value = speeds.length > 1 ? index / (speeds.length - 1) : 0;
                this._fanLabel.text = b.label(s.fan_speed);
            }
        }

        this._fault.visible = Boolean(s?.error);
        if (s) {
            // Presets
            if ([...this._presetButtons.keys()].join() !== s.presets.map(p => p.id).join()) {
                this._presetRow.destroy_all_children();
                this._presetButtons.clear();
                for (const preset of s.presets) {
                    const button = this._presetButton(preset);
                    this._presetButtons.set(preset.id, button);
                    this._presetRow.add_child(button);
                }
            }
            for (const preset of s.presets) {
                const button = this._presetButtons.get(preset.id);
                const active = b.presetActive(preset);
                button.style = active ? `background-color: ${button._tint}; color: white;` : '';
                button._icon.style = active ? 'color: white;' : `color: ${button._tint};`;
            }

            // Toggles
            for (const toggle of TOGGLES) {
                const button = this._toggleButtons.get(toggle.field);
                const on = s[toggle.field] === true;
                button.visible = Boolean(s[toggle.can]);
                button.reactive = b.isOn;
                button.opacity = b.isOn ? 255 : 115;
                button._circle.style = on ? `background-color: ${toggle.tint}; color: white;` : '';
            }

            // Timer
            this._timerMenu.visible = s.timers.length > 0;
            this._timerMenu.label.text = b.activeTimers.length
                ? `${TIMER_VERBS[b.activeTimers[0]]} ${duration(s[`timer_${b.activeTimers[0]}`] || 0)}`
                : 'Timer';
            this._timerMenu.icon.style = b.activeTimers.length ? 'color: #8b5cf6;' : '';
            this._rebuildTimerMenu();

            // Filter
            const used = s.filter_used_hours;
            this._filter.visible = used !== null && used !== undefined;
            if (this._filter.visible) {
                let text = `Filter used ${used} h`;
                if (s.filter_lifetime_hours)
                    text += ` · ${Math.max(0, Math.round(100 - 100 * used / s.filter_lifetime_hours))}% left`;
                this._filter.label.text = text;
            }
        }

        this._status.text = b.message;
        if (b.errorShown)
            this._status.add_style_class_name('lg-ac-error');
        else
            this._status.remove_style_class_name('lg-ac-error');
        this._syncing = false;
    }

    destroy() {
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        if (this._fanTimer) {
            GLib.source_remove(this._fanTimer);
            this._fanTimer = 0;
        }
        this._backend.destroy();
        this._settings = null;
        super.destroy();
    }
});

export default class LgAcExtension extends Extension {
    enable() {
        this._indicator = new Indicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
