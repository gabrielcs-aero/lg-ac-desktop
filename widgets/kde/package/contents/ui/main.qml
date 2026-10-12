/*
 * LG Air Conditioner — Plasma panel widget.
 *
 * All talking to LG goes through the `lg-ac-cli` command from this project, which prints
 * one line of JSON per call. Changes show up immediately and are sent one at a time in the
 * background; a few seconds after the last one the widget reads the unit back to confirm.
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
    property bool commandRunning: false
    property bool statusRunning: false
    property var jobs: ({})

    readonly property bool ready: acState !== null
    readonly property bool isOn: ready && acState.power === true
    readonly property real shownTarget: isNaN(pendingTarget) ? (ready && acState.target_temp !== null ? acState.target_temp : NaN) : pendingTarget
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
        const safe = args.every(a => /^[A-Za-z0-9_.-]+$/.test(String(a)))
        if (!safe) return null
        const cmd = Plasmoid.configuration.command + " " + args.join(" ")
        jobs[cmd] = { kind: kind, generation: generation }
        executable.connectSource(cmd)
        return cmd
    }

    function refresh() {
        if (statusRunning) return
        statusRunning = true
        run("status", ["status"])
    }

    function send(kind, args, changes) {
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
        commandRunning = true
        if (run(next.kind, next.args) === null) finished({ kind: next.kind }, { ok: false, error: "Invalid value" })
    }

    function finished(job, result) {
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

    function setPower(on) { send("power", ["power", on ? "on" : "off"], { power: on }) }
    function setMode(mode) {
        if (!ready || mode === acState.mode) return
        pendingTarget = NaN
        tempTimer.stop()
        send("mode", ["mode", mode], { mode: mode, can_set_temp: acState.temp_modes.indexOf(mode) >= 0 })
    }
    function setFan(speed) { if (ready && speed !== acState.fan_speed) send("fan", ["fan", speed], { fan_speed: speed }) }
    function nudge(direction) {
        if (!canSetTemp) return
        let value = shownTarget + direction * acState.temp_step
        if (acState.temp_min !== null) value = Math.max(value, acState.temp_min)
        if (acState.temp_max !== null) value = Math.min(value, acState.temp_max)
        generation++
        pendingTarget = value
        tempTimer.restart()
    }

    Timer { id: settleTimer; interval: 4000; onTriggered: root.refresh() }
    Timer {
        id: tempTimer
        interval: 700
        onTriggered: {
            const value = root.pendingTarget
            root.pendingTarget = NaN
            if (isNaN(value) || !root.ready || value === root.acState.target_temp) return
            root.send("temp", ["temp", value, "--mode", root.acState.mode], { target_temp: value })
        }
    }
    Timer {
        interval: Math.max(15, Plasmoid.configuration.pollSeconds) * 1000
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
        Layout.minimumHeight: Kirigami.Units.gridUnit * 12
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
                    PlasmaComponents.Label {
                        text: root.ready ? i18n("Room %1%2", root.formatTemp(root.acState.current_temp), root.unit) : ""
                        opacity: 0.7
                        font: Kirigami.Theme.smallFont
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

            // Temperature dial with −/+ on either side
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: Kirigami.Units.gridUnit * 10

                Item { Layout.fillWidth: true }
                StepButton { text: "−"; direction: -1; Accessible.name: i18n("Lower temperature") }

                Item {
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 10
                    Layout.fillHeight: true

                Canvas {
                    id: dial
                    readonly property real size: Math.min(width, height)
                    readonly property real fraction: {
                        if (!root.ready || isNaN(root.shownTarget) || root.acState.temp_min === null) return 0
                        return (root.shownTarget - root.acState.temp_min) / (root.acState.temp_max - root.acState.temp_min)
                    }
                    readonly property color accent: root.isOn && root.ready ? root.colorFor(root.acState.mode) : Kirigami.Theme.disabledTextColor
                    anchors.centerIn: parent
                    width: Kirigami.Units.gridUnit * 10
                    height: width
                    onFractionChanged: requestPaint()
                    onAccentChanged: requestPaint()
                    onPaint: {
                        const ctx = getContext("2d")
                        const line = Kirigami.Units.gridUnit * 0.6
                        const r = size / 2 - line
                        const start = Math.PI * 0.75, sweep = Math.PI * 1.5
                        ctx.reset()
                        ctx.lineCap = "round"
                        ctx.lineWidth = line
                        ctx.strokeStyle = Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.12)
                        ctx.beginPath(); ctx.arc(width / 2, height / 2, r, start, start + sweep); ctx.stroke()
                        ctx.strokeStyle = accent
                        ctx.beginPath(); ctx.arc(width / 2, height / 2, r, start, start + sweep * Math.max(0.01, fraction)); ctx.stroke()
                    }
                }

                ColumnLayout {
                    anchors.centerIn: dial
                    spacing: 0
                    PlasmaComponents.Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: root.formatTemp(root.shownTarget) + root.unit
                        font.pixelSize: Kirigami.Units.gridUnit * 2.6
                        font.weight: Font.Light
                        opacity: root.isOn ? 1 : 0.5
                    }
                    PlasmaComponents.Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: !root.ready ? "" : !root.isOn ? i18n("Off") : root.acState.can_set_temp ? i18n("Target") : i18n("No target in %1", root.label(root.acState.mode))
                        opacity: 0.65
                        font: Kirigami.Theme.smallFont
                    }
                }

                }

                StepButton { text: "+"; direction: 1; Accessible.name: i18n("Raise temperature") }
                Item { Layout.fillWidth: true }
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
