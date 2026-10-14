/*
 * LG Air Conditioner — Plasma panel widget.
 *
 * All talking to LG goes through the `lg-ac-cli` command from this project, which prints
 * one line of JSON per call. Changes show up immediately and are sent one at a time in the
 * background; changes made in quick succession go out as one `lg-ac-cli set` command, so the
 * unit beeps once. A few seconds after the last one the widget reads the unit back to confirm.
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    property var acState: null          // last state from `lg-ac-cli status`, plus optimistic changes
    property string message: "Loading…"
    property bool errorShown: false
    property real pendingTarget: NaN    // shown while −/+ clicks settle
    property int generation: 0          // bumped on every change so stale reads are dropped
    property int pendingCommands: 0
    property var queue: []
    property var batch: ({})            // `lg-ac-cli set` fields waiting to go out together
    property bool commandRunning: false
    property bool statusRunning: false
    property var jobs: ({})
    property bool timerPanelOpen: false
    property bool live: false           // LG is pushing changes to us (via `lg-ac-cli watch`)
    property bool watchRunning: false

    readonly property var timerVerbs: ({ stop: i18n("Off in"), start: i18n("On in"), sleep: i18n("Sleep, off in") })
    readonly property var activeTimers: ready ? ["stop", "sleep", "start"].filter(k => acState["timer_" + k] !== null && acState["timer_" + k] !== undefined) : []
    readonly property var presetTints: ({ sleep: "#8b5cf6", max_cool: "#3b82f6", eco: "#10b981" })
    function duration(minutes) {
        const h = Math.floor(minutes / 60), m = minutes % 60
        return h && m ? i18n("%1 h %2 min", h, m) : h ? i18n("%1 h", h) : i18n("%1 min", m)
    }
    function timerText(kind) {
        const m = acState["timer_" + kind]
        return timerVerbs[kind] + (m > 0 ? " " + duration(m) : "")
    }
    function presetActive(preset) {
        // While quick cooling, the unit's settings can match a preset without it being "on".
        return !boosting && Object.keys(preset.changes).filter(k => k.indexOf("timer_") !== 0 && k !== "can_set_temp").length === 0
    }

    readonly property bool ready: acState !== null
    readonly property bool isOn: ready && acState.power === true
    // Quick cool: full power until the room reaches the target, then hold it (`lg-ac-cli boost`).
    readonly property var quick: ready && acState.boost ? acState.boost : ({ active: false, available: false, target: null, label: "Quick cool" })
    readonly property bool boosting: quick.active
    readonly property bool quickHeat: quick.label === "Quick heat"
    // While boosting the unit is set to its coldest setting; show what we're aiming for instead.
    readonly property real shownTarget: !isNaN(pendingTarget) ? pendingTarget
        : boosting ? quick.target
        : ready && acState.target_temp !== null ? acState.target_temp : NaN
    readonly property bool canSetTemp: ready && isOn && acState.can_set_temp && !isNaN(shownTarget)
    readonly property string unit: ready ? "°" + acState.unit : "°"

    readonly property var modeIcons: ({ COOL: "mode-cool", HEAT: "mode-heat", AIR_DRY: "mode-dry", FAN: "mode-fan", AUTO: "mode-auto" })
    readonly property var modeColors: ({ COOL: "#3b82f6", HEAT: "#f97316", AIR_DRY: "#06b6d4", FAN: "#8b5cf6", AUTO: "#10b981" })

    function iconFor(mode) { return Qt.resolvedUrl("../icons/" + (modeIcons[mode] || "mode-auto") + ".svg") }
    function colorFor(mode) { return modeColors[mode] || Kirigami.Theme.highlightColor }
    function label(value) { return ready && acState.labels[value] ? acState.labels[value] : value }
    function formatTemp(t) { return isNaN(t) || t === null ? "–" : (Math.round(t * 2) / 2).toString() }

    Plasmoid.status: PlasmaCore.Types.ActiveStatus
    toolTipMainText: ready ? acState.alias : "LG Air Conditioner"
    toolTipSubText: !ready ? message
        : !isOn ? i18n("Off · Room %1%2", formatTemp(acState.current_temp), unit)
        : boosting ? i18n("%1 to %2%4 · Room %3%4", quick.label, formatTemp(shownTarget), formatTemp(acState.current_temp), unit)
        : i18n("%1 · Set to %2%4 · Room %3%4", label(acState.mode), formatTemp(shownTarget), formatTemp(acState.current_temp), unit)

    onExpandedChanged: if (expanded) refresh()

    // ---------- talking to lg-ac-cli ----------

    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            const job = root.jobs[sourceName]
            delete root.jobs[sourceName]
            disconnectSource(sourceName)
            let result
            try {
                result = JSON.parse(data["stdout"])
            } catch (e) {
                const stderr = (data["stderr"] || "").trim()
                result = { ok: false, error: stderr || i18n("lg-ac-cli didn't respond. Check the widget's command setting.") }
            }
            if (job) root.finished(job, result)
        }
    }

    function run(kind, args) {
        const safe = args.every(a => /^[A-Za-z0-9_.=-]+$/.test(String(a)))
        if (!safe) return null
        const cmd = Plasmoid.configuration.command + " " + args.join(" ")
        jobs[cmd] = { kind: kind, generation: generation }
        executable.connectSource(cmd)
        return cmd
    }

    // Long-poll for changes pushed by LG; restarts itself after each change or timeout.
    function watch() {
        if (watchRunning) return
        watchRunning = true
        run("watch", ["watch", "--timeout", "540"])
    }

    function refresh() {
        if (statusRunning) return
        statusRunning = true
        run("status", ["status"])
    }

    // Simple changes wait briefly so ones made together become a single command (one beep).
    function queueSet(fields, changes) {
        generation++
        errorShown = false
        settleTimer.stop()
        if (acState) acState = Object.assign({}, acState, changes)
        if (Object.keys(batch).length === 0) pendingCommands++  // the batch counts as one command
        batch = Object.assign({}, batch, fields)
        message = i18n("Sending…")
        batchTimer.restart()
    }

    function flushBatch() {
        batchTimer.stop()
        if (Object.keys(batch).length === 0) return
        const fields = batch
        batch = ({})
        const waiting = queue.find(c => c.kind === "set")
        if (waiting) { Object.assign(waiting.fields, fields); pendingCommands-- }  // join the one not yet sent
        else queue.push({ kind: "set", fields: fields })
        pump()
    }

    // Commands of their own (presets, quick cool).
    function send(kind, args, changes) {
        flushBatch()
        generation++
        errorShown = false
        settleTimer.stop()
        if (acState) acState = Object.assign({}, acState, changes)
        // A newer click of the same kind replaces one that hasn't been sent yet.
        const existing = queue.findIndex(c => c.kind === kind)
        if (existing >= 0) queue[existing].args = args
        else { queue.push({ kind: kind, args: args }); pendingCommands++ }
        message = i18n("Sending…")
        pump()
    }

    function pump() {
        if (commandRunning || queue.length === 0) return
        const next = queue.shift()
        const args = next.kind === "set" ? ["set"].concat(Object.keys(next.fields).map(k => k + "=" + next.fields[k])) : next.args
        commandRunning = true
        if (run(next.kind, args) === null) finished({ kind: next.kind }, { ok: false, error: "Invalid value" })
    }

    function finished(job, result) {
        if (job.kind === "watch") {
            watchRunning = false
            live = result.ok === true
            if (!result.ok) { watchRetry.restart(); return }
            if (result.changed && pendingCommands === 0 && isNaN(pendingTarget)) {
                acState = result.state
                if (!errorShown) message = i18n("Up to date")
            }
            Qt.callLater(watch)
            return
        }
        if (job.kind === "status") {
            statusRunning = false
            if (!result.ok) { showError(result.error); return }
            if (job.generation !== generation || pendingCommands > 0) return  // user changed something meanwhile
            acState = result.state
            if (!errorShown) message = i18n("Up to date")
            return
        }
        commandRunning = false
        pendingCommands--
        if (!result.ok) {
            showError(result.error)
            if (pendingCommands === 0) { generation++; refresh() }  // restore what the unit really has
        } else if (pendingCommands === 0) {
            if (!errorShown) message = i18n("Sent")
            settleTimer.restart()
        }
        pump()
    }

    function showError(text) {
        errorShown = true
        message = text
    }

    function setPower(on) { queueSet({ power: on ? "on" : "off" }, { power: on }) }
    function setMode(mode) {
        if (!ready || mode === acState.mode) return
        pendingTarget = NaN
        tempTimer.stop()
        queueSet({ mode: mode }, { mode: mode, can_set_temp: acState.temp_modes.indexOf(mode) >= 0 })
    }
    function setFan(speed) { if (ready && speed !== acState.fan_speed) queueSet({ fan: speed }, { fan_speed: speed }) }
    function setToggle(command, field, on) {
        if (ready && acState[field] !== on) queueSet({ [command]: on ? "on" : "off" }, { [field]: on })
    }
    function setTimer(kind, minutes) {
        queueSet({ ["timer-" + kind]: minutes }, { ["timer_" + kind]: minutes || null })
    }
    function applyPreset(preset) {
        if (!ready || Object.keys(preset.changes).length === 0) return
        pendingTarget = NaN
        tempTimer.stop()
        send("preset", ["preset", preset.id], preset.changes)
    }
    function startBoost() {
        if (!ready || isNaN(shownTarget)) return
        pendingTarget = NaN
        tempTimer.stop()
        send("boost", ["boost", shownTarget], { power: true, boost: Object.assign({}, quick, { active: true, target: shownTarget }) })
    }
    function stopBoost() {
        if (!boosting) return
        const target = quick.target
        pendingTarget = NaN
        tempTimer.stop()
        send("boost", ["boost", "off"], { target_temp: target, boost: Object.assign({}, quick, { active: false, target: null }) })
    }
    function nudge(direction) {
        if (!canSetTemp) return
        let value = shownTarget + direction * acState.temp_step
        if (acState.temp_min !== null) value = Math.max(value, acState.temp_min)
        if (acState.temp_max !== null) value = Math.min(value, acState.temp_max)
        generation++
        pendingTarget = value
        batchTimer.stop()  // other pending changes go out together with the temperature
        tempTimer.restart()
    }

    Timer { id: settleTimer; interval: 4000; onTriggered: root.refresh() }
    Timer { id: batchTimer; interval: 400; onTriggered: root.flushBatch() }
    Timer {
        id: tempTimer
        interval: 700
        onTriggered: {
            const value = root.pendingTarget
            root.pendingTarget = NaN
            if (isNaN(value) || !root.ready || (!root.boosting && value === root.acState.target_temp)) {
                root.flushBatch()
                return
            }
            if (root.boosting) {  // aim the quick cool somewhere else
                if (value !== root.quick.target)
                    root.send("boost", ["boost", value], { boost: Object.assign({}, root.quick, { target: value }) })
                else
                    root.flushBatch()
                return
            }
            root.queueSet({ temp: value }, { target_temp: value })
            root.flushBatch()  // the −/+ clicks have already settled
        }
    }
    Timer { id: watchRetry; interval: 5 * 60 * 1000; onTriggered: root.watch() }
    Component.onCompleted: watch()
    Timer {
        // While live, polling is only a safety net.
        interval: root.live ? 5 * 60 * 1000 : Math.max(15, Plasmoid.configuration.pollSeconds) * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: if (root.pendingCommands === 0) root.refresh()
    }

    // ---------- panel ----------

    compactRepresentation: MouseArea {
        id: compact
        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
        Layout.minimumWidth: vertical ? -1 : row.implicitWidth
        Layout.minimumHeight: vertical ? row.implicitHeight : -1
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded

        RowLayout {
            id: row
            anchors.centerIn: parent
            spacing: Kirigami.Units.smallSpacing
            opacity: root.isOn || !root.ready ? 1 : 0.55

            Kirigami.Icon {
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                source: root.ready ? root.iconFor(root.acState.mode) : Qt.resolvedUrl("../icons/mode-cool.svg")
                isMask: true
                color: root.isOn ? root.colorFor(root.acState.mode) : Kirigami.Theme.textColor
            }
            PlasmaComponents.Label {
                visible: !compact.vertical
                text: !root.ready ? "–" : root.isOn ? root.formatTemp(root.shownTarget) + "°" : i18n("Off")
                font.weight: Font.DemiBold
            }
        }
    }

    // ---------- popup ----------

    fullRepresentation: PlasmaExtras.Representation {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 19
        Layout.preferredWidth: Kirigami.Units.gridUnit * 21
        // Never shorter than the content, so rows can't be squeezed on top of each other.
        Layout.minimumHeight: root.ready ? controls.implicitHeight + Kirigami.Units.largeSpacing * 2 + Kirigami.Units.gridUnit * 3 : Kirigami.Units.gridUnit * 12
        Layout.preferredHeight: root.ready ? controls.implicitHeight + Kirigami.Units.largeSpacing * 2 + Kirigami.Units.gridUnit * 3 : Kirigami.Units.gridUnit * 16
        collapseMarginsHint: true

        header: PlasmaExtras.PlasmoidHeading {
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Kirigami.Units.smallSpacing
                anchors.rightMargin: Kirigami.Units.smallSpacing
                ColumnLayout {
                    spacing: 0
                    Layout.fillWidth: true
                    Kirigami.Heading {
                        level: 3
                        text: root.ready ? root.acState.alias : i18n("Air conditioner")
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    RowLayout {
                        spacing: Kirigami.Units.smallSpacing
                        PlasmaComponents.Label {
                            text: root.ready ? i18n("Room %1%2", root.formatTemp(root.acState.current_temp), root.unit) : ""
                            opacity: 0.7
                            font: Kirigami.Theme.smallFont
                        }
                        Rectangle {
                            visible: root.live
                            width: Kirigami.Units.smallSpacing * 1.5
                            height: width
                            radius: width / 2
                            color: "#10b981"
                        }
                        PlasmaComponents.Label {
                            visible: root.live
                            text: i18n("Live")
                            color: "#10b981"
                            font: Kirigami.Theme.smallFont
                            PlasmaComponents.ToolTip { text: i18n("Changes made with the remote or the ThinQ app show up here right away.") }
                        }
                    }
                }
                PlasmaComponents.Switch {
                    enabled: root.ready
                    checked: root.isOn
                    onToggled: root.setPower(checked)
                    Accessible.name: i18n("Power")
                }
            }
        }

        PlasmaExtras.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 2
            visible: !root.ready
            iconName: root.errorShown ? "dialog-warning" : "temperature-cold"
            text: root.errorShown ? i18n("Can't reach the air conditioner") : i18n("Loading…")
            explanation: root.errorShown ? root.message : ""
            helpfulAction: Kirigami.Action {
                icon.name: "configure"
                text: i18n("Open LG AC app")
                onTriggered: executable.connectSource(Plasmoid.configuration.appCommand + " >/dev/null 2>&1 &")
            }
        }

        ColumnLayout {
            id: controls
            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.largeSpacing
            visible: root.ready

            Kirigami.InlineMessage {
                Layout.fillWidth: true
                visible: root.ready && root.acState.error === true
                type: Kirigami.MessageType.Error
                text: i18n("The unit reports a fault. Check it, or look in the ThinQ app for details.")
            }

            // Target temperature with −/+ on either side
            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.largeSpacing

                Item { Layout.fillWidth: true }
                StepButton { text: "−"; direction: -1; Accessible.name: i18n("Lower temperature") }

                ColumnLayout {
                    spacing: 0
                    Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    PlasmaComponents.Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: root.formatTemp(root.shownTarget) + root.unit
                        font.pixelSize: Kirigami.Units.gridUnit * 3
                        font.weight: Font.Light
                        opacity: root.isOn ? 1 : 0.5
                    }
                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        spacing: Kirigami.Units.smallSpacing
                        Rectangle {
                            visible: root.isOn
                            width: Kirigami.Units.smallSpacing * 1.5
                            height: width
                            radius: width / 2
                            color: root.ready ? root.colorFor(root.acState.mode) : "transparent"
                        }
                        PlasmaComponents.Label {
                            text: !root.ready ? "" : !root.isOn ? i18n("Off")
                                : root.boosting ? i18n("%1 · then holds", root.quick.label)
                                : root.acState.can_set_temp ? i18n("Target · %1", root.label(root.acState.mode))
                                : i18n("No target in %1", root.label(root.acState.mode))
                            opacity: 0.65
                            font: Kirigami.Theme.smallFont
                        }
                    }
                }

                StepButton { text: "+"; direction: 1; Accessible.name: i18n("Raise temperature") }
                Item { Layout.fillWidth: true }
            }

            QuickButton {
                Layout.alignment: Qt.AlignHCenter
                visible: root.ready && root.quick.available && !isNaN(root.shownTarget)
            }

            // Presets
            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                Repeater {
                    model: root.ready ? root.acState.presets : []
                    delegate: PresetPill {
                        required property var modelData
                        preset: modelData
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                    }
                }
            }

            // Modes
            GridLayout {
                Layout.fillWidth: true
                columns: root.ready ? Math.min(root.acState.modes.length, 5) : 1
                columnSpacing: Kirigami.Units.smallSpacing
                Repeater {
                    model: root.ready ? root.acState.modes : []
                    delegate: AbstractChip {
                        required property string modelData
                        Layout.fillWidth: true
                        mode: modelData
                    }
                }
            }

            // Fan speed
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                RowLayout {
                    Kirigami.Icon {
                        source: Qt.resolvedUrl("../icons/mode-fan.svg")
                        isMask: true
                        color: Kirigami.Theme.textColor
                        Layout.preferredWidth: Kirigami.Units.iconSizes.small
                        Layout.preferredHeight: Kirigami.Units.iconSizes.small
                    }
                    PlasmaComponents.Label { text: i18n("Fan"); Layout.fillWidth: true }
                    PlasmaComponents.Label {
                        text: root.ready ? root.label(root.acState.fan_speed) : ""
                        font.weight: Font.DemiBold
                    }
                }
                PlasmaComponents.Slider {
                    id: fanSlider
                    Layout.fillWidth: true
                    from: 0
                    to: root.ready ? Math.max(1, root.acState.fan_speeds.length - 1) : 1
                    stepSize: 1
                    snapMode: QQC2.Slider.SnapAlways
                    value: root.ready ? Math.max(0, root.acState.fan_speeds.indexOf(root.acState.fan_speed)) : 0
                    onMoved: fanTimer.restart()
                    Timer {
                        id: fanTimer
                        interval: 400
                        onTriggered: root.setFan(root.acState.fan_speeds[Math.round(fanSlider.value)])
                    }
                }
            }

            // Swing, power save, display, timer
            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                RoundToggle {
                    iconName: "swing"; text: i18n("Swing"); tint: "#0ea5e9"
                    visible: root.ready && root.acState.can_swing
                    on: root.ready && root.acState.swing === true
                    enabled: root.isOn
                    onClicked: root.setToggle("swing", "swing", !on)
                }
                RoundToggle {
                    iconName: "power-save"; text: i18n("Power save"); tint: "#10b981"
                    visible: root.ready && root.acState.can_power_save
                    on: root.ready && root.acState.power_save === true
                    enabled: root.isOn
                    onClicked: root.setToggle("power-save", "power_save", !on)
                }
                RoundToggle {
                    iconName: "display"; text: i18n("Display"); tint: "#f59e0b"
                    visible: root.ready && root.acState.can_display_light
                    on: root.ready && root.acState.display_light === true
                    enabled: root.isOn
                    onClicked: root.setToggle("display", "display_light", !on)
                }
                RoundToggle {
                    iconName: "timer"; text: root.activeTimers.length ? root.timerText(root.activeTimers[0]) : i18n("Timer"); tint: "#8b5cf6"
                    visible: root.ready && root.acState.timers.length > 0
                    on: root.activeTimers.length > 0 || root.timerPanelOpen
                    onClicked: root.timerPanelOpen = !root.timerPanelOpen
                }
            }

            // Timer panel
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.timerPanelOpen && root.ready
                spacing: Kirigami.Units.smallSpacing
                readonly property string kind: root.isOn ? "stop" : "start"

                Repeater {
                    model: root.activeTimers
                    delegate: RowLayout {
                        required property string modelData
                        Layout.fillWidth: true
                        Rectangle { width: 8; height: 8; radius: 4; color: "#8b5cf6" }
                        PlasmaComponents.Label { text: root.timerText(modelData); Layout.fillWidth: true }
                        PlasmaComponents.ToolButton { text: i18n("Cancel"); onClicked: root.setTimer(modelData, 0) }
                    }
                }
                PlasmaComponents.Label {
                    text: parent.kind === "stop" ? i18n("Turn off in…") : i18n("Turn on in…")
                    opacity: 0.7
                    font: Kirigami.Theme.smallFont
                }
                GridLayout {
                    id: durations
                    Layout.fillWidth: true
                    columns: 3
                    columnSpacing: Kirigami.Units.smallSpacing
                    rowSpacing: Kirigami.Units.smallSpacing
                    readonly property string kind: parent.kind
                    Repeater {
                        model: [30, 60, 120, 240, 360, 480]
                        delegate: PlasmaComponents.Button {
                            required property int modelData
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            text: root.duration(modelData)
                            onClicked: { root.setTimer(durations.kind, modelData); root.timerPanelOpen = false }
                        }
                    }
                }
            }

            // Filter
            RowLayout {
                Layout.fillWidth: true
                visible: root.ready && root.acState.filter_used_hours !== null && root.acState.filter_used_hours !== undefined
                opacity: 0.6
                Kirigami.Icon {
                    source: Qt.resolvedUrl("../icons/filter.svg")
                    isMask: true
                    color: Kirigami.Theme.textColor
                    Layout.preferredWidth: Kirigami.Units.iconSizes.small
                    Layout.preferredHeight: Kirigami.Units.iconSizes.small
                }
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    font: Kirigami.Theme.smallFont
                    text: {
                        if (!root.ready) return ""
                        let s = i18n("Filter used %1 h", root.acState.filter_used_hours)
                        if (root.acState.filter_lifetime_hours)
                            s += " · " + i18n("%1% left", Math.max(0, Math.round(100 - 100 * root.acState.filter_used_hours / root.acState.filter_lifetime_hours)))
                        return s
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: root.message
                    wrapMode: Text.Wrap
                    opacity: root.errorShown ? 1 : 0.6
                    color: root.errorShown ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
                    font: Kirigami.Theme.smallFont
                }
                PlasmaComponents.ToolButton {
                    icon.name: "view-refresh"
                    onClicked: root.refresh()
                    PlasmaComponents.ToolTip { text: i18n("Refresh") }
                    Accessible.name: i18n("Refresh")
                }
            }
        }
    }

    component PresetPill: PlasmaComponents.AbstractButton {
        id: pill
        property var preset
        readonly property color tint: root.presetTints[preset.id] || Kirigami.Theme.highlightColor
        readonly property bool active: root.presetActive(preset)
        implicitHeight: Kirigami.Units.gridUnit * 1.8
        onClicked: root.applyPreset(preset)
        Accessible.name: preset.name
        Accessible.description: preset.description
        PlasmaComponents.ToolTip { text: pill.preset.description }
        background: Rectangle {
            radius: height / 2
            color: pill.active ? pill.tint
                 : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, pill.hovered ? 0.12 : 0.06)
            border.width: pill.visualFocus ? 2 : 0
            border.color: Kirigami.Theme.focusColor
            Behavior on color { ColorAnimation { duration: Kirigami.Units.shortDuration } }
        }
        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing
            Item { Layout.fillWidth: true }
            Kirigami.Icon {
                source: Qt.resolvedUrl("../icons/" + pill.preset.icon + ".svg")
                isMask: true
                color: pill.active ? "white" : pill.tint
                Layout.preferredWidth: Kirigami.Units.iconSizes.small
                Layout.preferredHeight: Kirigami.Units.iconSizes.small
            }
            PlasmaComponents.Label {
                text: pill.preset.name
                color: pill.active ? "white" : Kirigami.Theme.textColor
                font: Kirigami.Theme.smallFont
            }
            Item { Layout.fillWidth: true }
        }
    }

    // "Quick cool to 22°", or while it runs "Quick cooling · 26° → 22° | Stop".
    component QuickButton: PlasmaComponents.AbstractButton {
        id: qb
        readonly property color tint: root.quickHeat ? root.modeColors.HEAT : root.modeColors.COOL
        readonly property bool alreadyThere: !root.boosting && root.ready && root.acState.current_temp !== null
            && (root.quickHeat ? root.acState.current_temp >= root.shownTarget : root.acState.current_temp <= root.shownTarget)
        enabled: root.boosting || !alreadyThere
        implicitHeight: Kirigami.Units.gridUnit * 1.8
        implicitWidth: qbRow.implicitWidth + Kirigami.Units.gridUnit * 1.6
        onClicked: root.boosting ? root.stopBoost() : root.startBoost()
        Accessible.name: root.boosting ? i18n("Stop %1", root.quick.label) : root.quick.label
        PlasmaComponents.ToolTip {
            text: root.boosting
                ? i18n("Running at full power until the room reaches %1°, then it holds that. Click to hold it now.", root.formatTemp(root.quick.target))
                : qb.alreadyThere ? i18n("The room is already at %1°.", root.formatTemp(root.shownTarget))
                : i18n("Full power until the room reaches %1°, then holds it there.", root.formatTemp(root.shownTarget))
        }
        background: Rectangle {
            radius: height / 2
            color: root.boosting ? qb.tint
                 : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, qb.hovered ? 0.12 : 0.06)
            border.width: qb.visualFocus ? 2 : 0
            border.color: Kirigami.Theme.focusColor
            Behavior on color { ColorAnimation { duration: Kirigami.Units.shortDuration } }
            Rectangle {  // breathes while boosting
                anchors.fill: parent
                anchors.margins: -3
                radius: height / 2
                color: "transparent"
                border.color: qb.tint
                border.width: 2
                visible: root.boosting
                SequentialAnimation on opacity {
                    running: root.boosting && root.expanded
                    loops: Animation.Infinite
                    NumberAnimation { from: 0.5; to: 0; duration: 1400; easing.type: Easing.OutQuad }
                    PauseAnimation { duration: 300 }
                }
            }
        }
        contentItem: Item {
            implicitWidth: qbRow.implicitWidth
            implicitHeight: qbRow.implicitHeight
            RowLayout {
                id: qbRow
                anchors.centerIn: parent
                spacing: Kirigami.Units.smallSpacing
                opacity: qb.enabled ? 1 : 0.45
                Kirigami.Icon {
                    source: Qt.resolvedUrl("../icons/" + (root.quickHeat ? "quick-heat" : "quick-cool") + ".svg")
                    isMask: true
                    color: root.boosting ? "white" : qb.tint
                    Layout.preferredWidth: Kirigami.Units.iconSizes.small
                    Layout.preferredHeight: Kirigami.Units.iconSizes.small
                }
                PlasmaComponents.Label {
                    text: !root.ready ? ""
                        : root.boosting ? i18n("%1 · %2° → %3°", root.quickHeat ? i18n("Quick heating") : i18n("Quick cooling"),
                                               root.formatTemp(root.acState.current_temp), root.formatTemp(root.quick.target))
                        : i18n("%1 to %2°", root.quick.label, root.formatTemp(root.shownTarget))
                    color: root.boosting ? "white" : Kirigami.Theme.textColor
                    font: Kirigami.Theme.smallFont
                }
                Rectangle {
                    visible: root.boosting
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 0.8
                    color: "white"
                    opacity: 0.4
                }
                PlasmaComponents.Label {
                    visible: root.boosting
                    text: i18n("Stop")
                    color: "white"
                    font: Kirigami.Theme.smallFont
                }
            }
        }
    }

    // A round icon toggle with a caption, filled with its tint when on.
    component RoundToggle: PlasmaComponents.AbstractButton {
        id: toggle
        property string iconName
        property color tint
        property bool on: false
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        implicitHeight: Kirigami.Units.gridUnit * 3.6
        Accessible.name: text
        Accessible.role: Accessible.CheckBox
        Accessible.checked: on
        background: Item {}
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.smallSpacing / 2
            opacity: toggle.enabled ? 1 : 0.45
            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                width: Kirigami.Units.gridUnit * 2.2
                height: width
                radius: width / 2
                color: toggle.on ? toggle.tint
                     : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, toggle.hovered ? 0.14 : 0.07)
                border.width: toggle.visualFocus ? 2 : 0
                border.color: Kirigami.Theme.focusColor
                Behavior on color { ColorAnimation { duration: Kirigami.Units.shortDuration } }
                Kirigami.Icon {
                    anchors.centerIn: parent
                    width: Kirigami.Units.iconSizes.smallMedium
                    height: width
                    source: Qt.resolvedUrl("../icons/" + toggle.iconName + ".svg")
                    isMask: true
                    color: toggle.on ? "white" : Kirigami.Theme.textColor
                }
            }
            PlasmaComponents.Label {
                Layout.alignment: Qt.AlignHCenter
                Layout.maximumWidth: toggle.width
                text: toggle.text
                font: Kirigami.Theme.smallFont
                elide: Text.ElideRight
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    component StepButton: PlasmaComponents.AbstractButton {
        id: step
        property int direction
        Layout.preferredWidth: Kirigami.Units.gridUnit * 2.4
        Layout.preferredHeight: Layout.preferredWidth
        Layout.alignment: Qt.AlignVCenter
        enabled: root.canSetTemp
        autoRepeat: true
        onClicked: root.nudge(direction)
        background: Rectangle {
            radius: width / 2
            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b,
                           step.pressed ? 0.22 : step.hovered ? 0.14 : 0.07)
            border.width: step.visualFocus ? 2 : 0
            border.color: Kirigami.Theme.focusColor
        }
        contentItem: PlasmaComponents.Label {
            text: step.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: Kirigami.Units.gridUnit * 1.3
            opacity: step.enabled ? 1 : 0.35
        }
    }

    // A mode button: icon over label, filled with the mode's colour when selected.
    component AbstractChip: PlasmaComponents.AbstractButton {
        id: chip
        property string mode
        readonly property bool selected: root.ready && root.acState.mode === mode
        implicitHeight: Kirigami.Units.gridUnit * 3.2
        enabled: root.isOn
        onClicked: root.setMode(mode)
        Accessible.name: root.label(mode)
        Accessible.role: Accessible.RadioButton
        Accessible.checked: selected

        background: Rectangle {
            radius: Kirigami.Units.cornerRadius !== undefined ? Kirigami.Units.cornerRadius * 2 : 8
            color: chip.selected ? root.colorFor(chip.mode)
                 : chip.hovered ? Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.10)
                 : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.05)
            border.width: chip.visualFocus ? 2 : 0
            border.color: Kirigami.Theme.focusColor
            Behavior on color { ColorAnimation { duration: Kirigami.Units.shortDuration } }
        }
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.smallSpacing / 2
            opacity: chip.enabled ? 1 : 0.5
            Kirigami.Icon {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                source: root.iconFor(chip.mode)
                isMask: true
                color: chip.selected ? "white" : Kirigami.Theme.textColor
            }
            PlasmaComponents.Label {
                Layout.alignment: Qt.AlignHCenter
                text: root.label(chip.mode)
                color: chip.selected ? "white" : Kirigami.Theme.textColor
                font: Kirigami.Theme.smallFont
            }
        }
    }
}
