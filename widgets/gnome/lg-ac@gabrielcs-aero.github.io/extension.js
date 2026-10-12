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

function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
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
        this._pollId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, poll, () => {
            this._backend.refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._backend.refresh();
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
        this._room.label.text = s ? `Room ${formatTemp(s.current_temp)}°${s.unit}` : '';

        this._targetLabel.text = s ? `${formatTemp(b.shownTarget)}°${s.unit}` : '–';
        this._targetLabel.opacity = b.isOn ? 255 : 128;
        this._targetCaption.text = !s ? '' : !b.isOn ? 'Off' : s.can_set_temp ? 'Target' : `No target in ${b.label(s.mode)}`;
        this._minus.reactive = this._plus.reactive = b.canSetTemp;
        this._minus.opacity = this._plus.opacity = b.canSetTemp ? 255 : 90;
        this._dial.queue_repaint();

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
