// LG AC Desktop — main window. State and actions come from `acModel` (lg_ac/app.py).
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.impl
import QtQuick.Layouts
import QtQuick.Shapes

ApplicationWindow {
    id: win
    width: 880
    height: 600
    minimumWidth: 360
    minimumHeight: 560
    visible: true
    title: acModel.alias + (acModel.isDemo ? " (demo)" : "")
    color: t.bg

    // ---------- theme ----------
    // Follows the desktop's light/dark setting; LG_AC_THEME=dark|light overrides it.
    readonly property bool wide: width >= 760
    readonly property bool dark: themeOverride === "dark" || (themeOverride !== "light" && Qt.styleHints.colorScheme === Qt.Dark)
    QtObject {
        id: t
        readonly property color bg: win.dark ? "#16181d" : "#f4f5f7"
        readonly property color card: win.dark ? "#1f2229" : "#ffffff"
        readonly property color raised: win.dark ? "#2a2e37" : "#eceef2"
        readonly property color text: win.dark ? "#f1f3f6" : "#14161a"
        readonly property color muted: win.dark ? "#9aa1ad" : "#646b78"
        readonly property color track: win.dark ? "#2f343e" : "#e3e6eb"
        readonly property color danger: win.dark ? "#ff7b63" : "#c2410c"
        readonly property color off: win.dark ? "#4b5160" : "#b8bec9"
    }
    readonly property var modeColors: ({ COOL: "#3b82f6", HEAT: "#f97316", AIR_DRY: "#06b6d4", FAN: "#8b5cf6", AUTO: "#10b981" })
    readonly property var modeIcons: ({ COOL: "mode-cool", HEAT: "mode-heat", AIR_DRY: "mode-dry", FAN: "mode-fan", AUTO: "mode-auto" })

    // ---------- shorthands ----------
    readonly property var ac: acModel.ac
    readonly property bool ready: ac.modes !== undefined
    readonly property bool isOn: ready && ac.power === true
    readonly property color accent: isOn ? (modeColors[ac.mode] || "#3b82f6") : t.off
    readonly property bool canSetTemp: isOn && ac.can_set_temp && ac.shown_target !== null
    readonly property var quick: ready ? ac.boost : ({ active: false, available: false, target: null, label: "Quick cool" })
    readonly property bool boosting: quick.active
    function iconUrl(name) { return iconDir + name + ".svg" }
    function label(v) { return ready && ac.labels[v] ? ac.labels[v] : v }
    function fmt(v) { return v === null || v === undefined ? "–" : String(Math.round(v * 2) / 2) }
    function duration(minutes) {
        const h = Math.floor(minutes / 60), m = minutes % 60
        return h && m ? h + " h " + m + " min" : h ? h + " h" : m + " min"
    }
    readonly property var timerVerbs: ({ stop: "Off in", start: "On in", sleep: "Sleep, off in" })
    readonly property var activeTimers: {
        if (!ready) return []
        return ["stop", "sleep", "start"].filter(k => ac["timer_" + k] !== null && ac["timer_" + k] !== undefined)
    }
    function timerText(kind) {
        const m = ac["timer_" + kind]
        return timerVerbs[kind] + (m > 0 ? " " + duration(m) : "")
    }


    // ---------- main view ----------
    Item {
        anchors.fill: parent
        visible: !acModel.needsSetup

        Flickable {
            id: scroller
            anchors { left: parent.left; right: parent.right; top: parent.top; bottom: footer.top; bottomMargin: 8 }
            contentHeight: mainColumn.implicitHeight + 40
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            ColumnLayout {
                id: mainColumn
                x: 20
                y: 20
                width: scroller.width - 40
                spacing: 14

                // Header
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        RowLayout {
                            spacing: 8
                            Label {
                                text: acModel.alias
                                color: t.text
                                font.pixelSize: 20
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                                Layout.maximumWidth: win.wide ? 420 : 220
                            }
                            Rectangle {
                                visible: acModel.isDemo
                                radius: 6
                                color: t.raised
                                implicitWidth: demoLabel.implicitWidth + 12
                                implicitHeight: demoLabel.implicitHeight + 4
                                Label { id: demoLabel; anchors.centerIn: parent; text: "Demo"; color: t.muted; font.pixelSize: 11 }
                            }
                            Rectangle {
                                visible: acModel.isLive
                                radius: 6
                                color: Qt.rgba(0.06, 0.73, 0.51, 0.15)
                                implicitWidth: liveRow.implicitWidth + 12
                                implicitHeight: liveRow.implicitHeight + 4
                                ToolTip.visible: liveHover.hovered
                                ToolTip.text: "Changes made with the remote or the ThinQ app show up here right away."
                                HoverHandler { id: liveHover }
                                RowLayout {
                                    id: liveRow
                                    anchors.centerIn: parent
                                    spacing: 5
                                    Rectangle {
                                        width: 6; height: 6; radius: 3
                                        color: "#10b981"
                                        SequentialAnimation on opacity {
                                            loops: Animation.Infinite
                                            NumberAnimation { to: 0.35; duration: 1200; easing.type: Easing.InOutSine }
                                            NumberAnimation { to: 1; duration: 1200; easing.type: Easing.InOutSine }
                                        }
                                    }
                                    Label { text: "Live"; color: "#10b981"; font.pixelSize: 11; font.weight: Font.DemiBold }
                                }
                            }
                        }
                        Label {
                            text: ready ? "Room " + win.fmt(ac.current_temp) + "°" + ac.unit : " "
                            color: t.muted
                            font.pixelSize: 13
                        }
                    }
                    Item { Layout.fillWidth: true }
                    IconButton { source: win.iconUrl("settings"); onClicked: acModel.openSetup(); Accessible.name: "Settings" }
                    PowerSwitch {}
                }

                // Fault reported by the unit
                Rectangle {
                    Layout.fillWidth: true
                    visible: ready && ac.error === true
                    radius: 12
                    color: Qt.rgba(t.danger.r, t.danger.g, t.danger.b, 0.12)
                    implicitHeight: faultRow.implicitHeight + 20
                    RowLayout {
                        id: faultRow
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 10
                        IconImage { source: win.iconUrl("warning"); color: t.danger; sourceSize: Qt.size(18, 18) }
                        Label {
                            Layout.fillWidth: true
                            text: "The unit reports a fault. Check it, or look in the ThinQ app for details."
                            color: t.danger
                            font.pixelSize: 13
                            wrapMode: Text.Wrap
                        }
                    }
                }

                // Two columns on a wide window (dial + presets | controls), one when narrow.
                GridLayout {
                    Layout.fillWidth: true
                    columns: win.wide ? 2 : 1
                    columnSpacing: 32
                    rowSpacing: 14

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        Layout.alignment: Qt.AlignTop
                        spacing: 14

                        // Dial
                        Item {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 250

                            Item {
                                id: dial
                                width: 230
                                height: 230
                                anchors.centerIn: parent
                                readonly property real lineWidth: 14
                                readonly property real fraction: {
                                    if (!ready || ac.shown_target === null || ac.temp_min === null) return 0
                                    return Math.max(0, Math.min(1, (ac.shown_target - ac.temp_min) / (ac.temp_max - ac.temp_min)))
                                }
                                property real shownFraction: fraction
                                Behavior on shownFraction { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

                                Shape {
                                    anchors.fill: parent
                                    layer.enabled: true
                                    layer.samples: 4
                                    ShapePath {
                                        strokeColor: t.track
                                        strokeWidth: dial.lineWidth
                                        fillColor: "transparent"
                                        capStyle: ShapePath.RoundCap
                                        PathAngleArc {
                                            centerX: dial.width / 2; centerY: dial.height / 2
                                            radiusX: dial.width / 2 - dial.lineWidth; radiusY: radiusX
                                            startAngle: 135; sweepAngle: 270
                                        }
                                    }
                                    ShapePath {
                                        strokeColor: win.accent
                                        strokeWidth: dial.lineWidth
                                        fillColor: "transparent"
                                        capStyle: ShapePath.RoundCap
                                        PathAngleArc {
                                            centerX: dial.width / 2; centerY: dial.height / 2
                                            radiusX: dial.width / 2 - dial.lineWidth; radiusY: radiusX
                                            startAngle: 135; sweepAngle: Math.max(0.5, 270 * dial.shownFraction)
                                        }
                                    }
                                }

                                ColumnLayout {
                                    anchors.centerIn: parent
                                    spacing: 0
                                    Label {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: ready ? win.fmt(ac.shown_target) + "°" : "–"
                                        color: isOn ? t.text : t.muted
                                        font.pixelSize: 64
                                        font.weight: Font.Light
                                    }
                                    Label {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: !ready ? "" : !isOn ? "Off" : boosting ? quick.label + " · then holds"
                                            : ac.can_set_temp ? "Target · °" + ac.unit : "No target in " + win.label(ac.mode)
                                        color: t.muted
                                        font.pixelSize: 13
                                    }
                                }
                            }

                            StepButton { text: "−"; direction: -1; anchors.verticalCenter: dial.verticalCenter; anchors.right: dial.left; anchors.rightMargin: -4 }
                            StepButton { text: "+"; direction: 1; anchors.verticalCenter: dial.verticalCenter; anchors.left: dial.right; anchors.leftMargin: -4 }
                        }

                        QuickButton {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.topMargin: -6
                            visible: ready && quick.available && ac.shown_target !== null
                        }

                        // Presets
                        SectionLabel { text: "Presets"; visible: ready }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            visible: ready
                            Repeater {
                                model: ready ? ac.presets : []
                                delegate: PresetPill {
                                    required property var modelData
                                    preset: modelData
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        Layout.alignment: Qt.AlignTop
                        spacing: 14

                        SectionLabel { text: "Mode" }
                        // Modes
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Repeater {
                                model: ready ? ac.modes : []
                                delegate: ModeChip {
                                    required property string modelData
                                    mode: modelData
                                    Layout.fillWidth: true
                                }
                            }
                        }

                        // Fan speed
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: fanColumn.implicitHeight + 28
                            radius: 16
                            color: t.card
                            opacity: isOn ? 1 : 0.6
                            ColumnLayout {
                                id: fanColumn
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 10
                                RowLayout {
                                    IconImage { source: win.iconUrl("mode-fan"); color: t.muted; sourceSize: Qt.size(18, 18) }
                                    Label { text: "Fan speed"; color: t.muted; font.pixelSize: 13; Layout.fillWidth: true }
                                    Label { text: ready ? win.label(fanSlider.speed) : ""; color: t.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                                }
                                FanSlider { id: fanSlider; Layout.fillWidth: true }
                            }
                        }

                        // Extras
                        SectionLabel { text: "More"; visible: ready }
                        GridLayout {
                            Layout.fillWidth: true
                            visible: ready
                            columns: 2
                            columnSpacing: 10
                            rowSpacing: 10
                            ToggleTile {
                                title: "Swing"
                                iconName: "swing"
                                tint: "#0ea5e9"
                                visible: ready && ac.can_swing
                                on: ready && ac.swing === true
                                subtitle: on ? "Up and down" : "Off"
                                enabled: isOn
                                onClicked: acModel.setSwing(!on)
                            }
                            ToggleTile {
                                title: "Power save"
                                iconName: "power-save"
                                tint: "#10b981"
                                visible: ready && ac.can_power_save
                                on: ready && ac.power_save === true
                                subtitle: on ? "On" : "Off"
                                enabled: isOn
                                onClicked: acModel.setPowerSave(!on)
                            }
                            ToggleTile {
                                title: "Display"
                                iconName: "display"
                                tint: "#f59e0b"
                                visible: ready && ac.can_display_light
                                on: ready && ac.display_light === true
                                subtitle: on ? "Light on" : "Light off"
                                enabled: isOn
                                onClicked: acModel.setDisplayLight(!on)
                            }
                            ToggleTile {
                                title: "Timer"
                                iconName: "timer"
                                tint: "#8b5cf6"
                                visible: ready && ac.timers.length > 0
                                on: win.activeTimers.length > 0
                                subtitle: on ? win.timerText(win.activeTimers[0]) : "Not set"
                                onClicked: timerPopup.open()
                            }
                        }

                        // Filter
                        RowLayout {
                            Layout.fillWidth: true
                            visible: ready && ac.filter_used_hours !== null && ac.filter_used_hours !== undefined
                            spacing: 8
                            opacity: 0.85
                            IconImage { source: win.iconUrl("filter"); color: t.muted; sourceSize: Qt.size(16, 16) }
                            Label {
                                Layout.fillWidth: true
                                color: t.muted
                                font.pixelSize: 12
                                text: {
                                    if (!ready) return ""
                                    let s = "Filter used " + ac.filter_used_hours + " h"
                                    if (ac.filter_lifetime_hours)
                                        s += " · " + Math.max(0, Math.round(100 - 100 * ac.filter_used_hours / ac.filter_lifetime_hours)) + "% left"
                                    return s
                                }
                            }
                        }
                    }
                }
            }
        }

        // Status
        RowLayout {
            id: footer
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 20; topMargin: 0 }
            Label {
                Layout.fillWidth: true
                text: acModel.message
                color: acModel.errorShown ? t.danger : t.muted
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }
            IconButton { source: win.iconUrl("refresh"); onClicked: acModel.refresh(); enabled: ready; Accessible.name: "Refresh" }
        }
    }

    // ---------- setup view ----------
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 14
        visible: acModel.needsSetup

        IconImage { source: win.iconUrl("lg-ac"); sourceSize: Qt.size(56, 56); Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 12 }
        Label {
            text: "Connect to LG ThinQ"
            color: t.text
            font.pixelSize: 22
            font.weight: Font.DemiBold
            Layout.alignment: Qt.AlignHCenter
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: t.muted
            font.pixelSize: 13
            textFormat: Text.StyledText
            linkColor: "#3b82f6"
            text: "1. Open <a href='" + acModel.tokenUrl + "'>connect-pat.lgthinq.com</a> and sign in with your ThinQ account.<br>"
                + "2. Create a token that can list devices, read their status and control them.<br>"
                + "3. Paste it below."
            onLinkActivated: link => Qt.openUrlExternally(link)
            HoverHandler { cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor }
        }
        Field {
            id: tokenField
            placeholderText: acModel.hasToken ? "Token (leave empty to keep the current one)" : "Personal Access Token"
            echoMode: TextInput.Password
        }
        Field { id: countryField; text: acModel.countryCode; placeholderText: "Country code, e.g. BR"; maximumLength: 2 }
        Label { id: setupError; color: t.danger; font.pixelSize: 12; visible: text !== "" }
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            PillButton {
                text: "Cancel"
                visible: acModel.hasToken || acModel.isDemo
                onClicked: { setupError.text = ""; acModel.cancelSetup() }
            }
            PillButton {
                text: "Connect"
                primary: true
                Layout.fillWidth: true
                onClicked: {
                    setupError.text = acModel.saveSetup(tokenField.text, countryField.text)
                    if (setupError.text === "") tokenField.text = ""
                }
            }
        }
        Item { Layout.fillHeight: true }
    }

    // ---------- timer sheet ----------
    Popup {
        id: timerPopup
        objectName: "timerPopup"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(win.width - 32, 360)
        modal: true
        focus: true
        padding: 20
        property string kind: "stop"
        property int minutes: 60
        readonly property var kinds: ready ? ["stop", "start", "sleep"].filter(k => ac.timers.indexOf(k) >= 0) : []
        readonly property var kindLabels: ({ stop: "Turn off", start: "Turn on", sleep: "Sleep" })
        onAboutToShow: {
            kind = isOn ? (kinds.indexOf("stop") >= 0 ? "stop" : kinds[0]) : (kinds.indexOf("start") >= 0 ? "start" : kinds[0])
            minutes = 60
        }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.45) }
        background: Rectangle { radius: 22; color: t.card; border.color: t.track; border.width: 1 }
        enter: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 160 }
            NumberAnimation { property: "scale"; from: 0.94; to: 1; duration: 160; easing.type: Easing.OutCubic }
        }
        exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 120 } }

        contentItem: ColumnLayout {
            spacing: 16

            RowLayout {
                Layout.fillWidth: true
                IconImage { source: win.iconUrl("timer"); color: "#8b5cf6"; sourceSize: Qt.size(22, 22) }
                Label { text: "Timer"; color: t.text; font.pixelSize: 18; font.weight: Font.DemiBold; Layout.fillWidth: true }
                MiniStep { text: "×"; onClicked: timerPopup.close(); Accessible.name: "Close" }
            }

            // Turn off / Turn on / Sleep
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 40
                radius: 20
                color: t.raised
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 4
                    spacing: 4
                    Repeater {
                        model: timerPopup.kinds
                        delegate: AbstractButton {
                            id: seg
                            required property string modelData
                            readonly property bool selected: timerPopup.kind === modelData
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            onClicked: timerPopup.kind = modelData
                            Accessible.role: Accessible.PageTab
                            Accessible.name: timerPopup.kindLabels[modelData]
                            background: Rectangle {
                                radius: 16
                                color: seg.selected ? t.card : "transparent"
                                border.color: seg.selected ? t.track : "transparent"
                                Behavior on color { ColorAnimation { duration: 150 } }
                            }
                            contentItem: Label {
                                text: timerPopup.kindLabels[seg.modelData]
                                color: seg.selected ? t.text : t.muted
                                font.pixelSize: 13
                                font.weight: seg.selected ? Font.DemiBold : Font.Normal
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }
                }
            }

            // Quick durations
            GridLayout {
                Layout.fillWidth: true
                columns: 3
                rowSpacing: 8
                columnSpacing: 8
                Repeater {
                    model: [30, 60, 120, 240, 360, 480]
                    delegate: AbstractButton {
                        id: chip
                        required property int modelData
                        readonly property bool selected: timerPopup.minutes === modelData
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1  // equal columns regardless of label length
                        implicitHeight: 38
                        hoverEnabled: true
                        onClicked: timerPopup.minutes = modelData
                        background: Rectangle {
                            radius: 12
                            color: chip.selected ? "#8b5cf6" : chip.hovered ? t.raised : t.bg
                            Behavior on color { ColorAnimation { duration: 150 } }
                        }
                        contentItem: Label {
                            text: win.duration(chip.modelData)
                            color: chip.selected ? "white" : t.text
                            font.pixelSize: 13
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
            }

            // Fine-tune
            RowLayout {
                Layout.fillWidth: true
                MiniStep { text: "−"; enabled: timerPopup.minutes > 10; autoRepeat: true; onClicked: timerPopup.minutes = Math.max(10, timerPopup.minutes - 10); Accessible.name: "Shorter" }
                Label {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    text: win.duration(timerPopup.minutes)
                    color: t.text
                    font.pixelSize: 26
                    font.weight: Font.Light
                }
                MiniStep { text: "+"; enabled: timerPopup.minutes < 1440; autoRepeat: true; onClicked: timerPopup.minutes = Math.min(1440, timerPopup.minutes + 10); Accessible.name: "Longer" }
            }

            PillButton {
                Layout.fillWidth: true
                primary: true
                text: "Start " + timerPopup.kindLabels[timerPopup.kind].toLowerCase() + " timer"
                onClicked: { acModel.setTimer(timerPopup.kind, timerPopup.minutes); timerPopup.close() }
            }

            // Running timers
            Repeater {
                model: win.activeTimers
                delegate: RowLayout {
                    required property string modelData
                    Layout.fillWidth: true
                    spacing: 10
                    Rectangle { width: 8; height: 8; radius: 4; color: "#8b5cf6" }
                    Label { text: win.timerText(modelData); color: t.text; font.pixelSize: 13; Layout.fillWidth: true }
                    AbstractButton {
                        id: cancelBtn
                        implicitWidth: cancelLabel.implicitWidth + 20
                        implicitHeight: 28
                        hoverEnabled: true
                        onClicked: acModel.setTimer(modelData, 0)
                        background: Rectangle { radius: 14; color: cancelBtn.hovered ? t.raised : "transparent"; border.color: t.track }
                        contentItem: Label { id: cancelLabel; text: "Cancel"; color: t.danger; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                }
            }
        }
    }

    // ---------- components ----------

    component SectionLabel: Label {
        color: t.muted
        font.pixelSize: 12
        font.weight: Font.DemiBold
        font.letterSpacing: 0.4
        Layout.topMargin: 4
    }

    component PresetPill: AbstractButton {
        id: pill
        property var preset
        readonly property var tints: ({ sleep: "#8b5cf6", max_cool: "#3b82f6", eco: "#10b981" })
        readonly property color tint: tints[preset.id] || "#3b82f6"
        // A preset counts as active when nothing but its timer would change.
        readonly property bool active: !boosting && Object.keys(preset.changes).filter(k => k.indexOf("timer_") !== 0 && k !== "can_set_temp").length === 0
        implicitHeight: 42
        hoverEnabled: true
        onClicked: acModel.applyPreset(preset.id)
        Accessible.name: preset.name
        Accessible.description: preset.description
        ToolTip.visible: hovered
        ToolTip.delay: 500
        ToolTip.text: preset.description
        background: Rectangle {
            radius: 21
            color: pill.active ? pill.tint : pill.hovered ? t.raised : t.card
            border.color: pill.active ? pill.tint : t.track
            border.width: 1
            Behavior on color { ColorAnimation { duration: 200 } }
        }
        contentItem: RowLayout {
            spacing: 6
            Item { Layout.fillWidth: true }
            IconImage {
                source: win.iconUrl(pill.preset.icon)
                sourceSize: Qt.size(16, 16)
                color: pill.active ? "white" : pill.tint
                Behavior on color { ColorAnimation { duration: 200 } }
            }
            Label {
                text: pill.preset.name
                color: pill.active ? "white" : t.text
                font.pixelSize: 13
                font.weight: Font.DemiBold
                Behavior on color { ColorAnimation { duration: 200 } }
            }
            Item { Layout.fillWidth: true }
        }
    }

    // "Quick cool to 22°": full power until the room gets there, then hold it (lg_ac/boost.py).
    component QuickButton: AbstractButton {
        id: qb
        readonly property color tint: quick.label === "Quick heat" ? modeColors.HEAT : modeColors.COOL
        readonly property bool alreadyThere: !boosting && ac.current_temp !== null
            && (quick.label === "Quick heat" ? ac.current_temp >= ac.shown_target : ac.current_temp <= ac.shown_target)
        enabled: boosting || !alreadyThere
        implicitHeight: 40
        implicitWidth: qbRow.implicitWidth + 32
        hoverEnabled: true
        onClicked: boosting ? acModel.stopBoost() : acModel.startBoost()
        Accessible.name: boosting ? "Stop " + quick.label.toLowerCase() : quick.label
        ToolTip.visible: hovered
        ToolTip.delay: 500
        ToolTip.text: boosting
            ? "Running at full power until the room reaches " + win.fmt(quick.target) + "°, then it holds that. Click to hold it now."
            : alreadyThere ? "The room is already at " + win.fmt(ac.shown_target) + "°."
            : "Full power until the room reaches " + win.fmt(ac.shown_target) + "°, then holds it there."
        background: Rectangle {
            radius: height / 2
            color: boosting ? qb.tint : qb.hovered ? t.raised : t.card
            border.color: boosting ? qb.tint : t.track
            border.width: 1
            Behavior on color { ColorAnimation { duration: 200 } }
            // A soft glow that breathes while boosting.
            Rectangle {
                anchors.fill: parent
                anchors.margins: -4
                radius: height / 2
                color: "transparent"
                border.color: qb.tint
                border.width: 2
                visible: boosting
                SequentialAnimation on opacity {
                    running: boosting
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
                spacing: 8
                opacity: qb.enabled ? 1 : 0.45
                IconImage {
                    source: win.iconUrl(quick.label === "Quick heat" ? "quick-heat" : "quick-cool")
                    sourceSize: Qt.size(18, 18)
                    color: boosting ? "white" : qb.tint
                }
                Label {
                    text: boosting
                        ? quick.label + "ing · " + win.fmt(ac.current_temp) + "° → " + win.fmt(quick.target) + "°"
                        : quick.label + " to " + win.fmt(ac.shown_target) + "°"
                    color: boosting ? "white" : t.text
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                Rectangle {
                    visible: boosting
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 16
                    color: "white"
                    opacity: 0.4
                }
                Label {
                    visible: boosting
                    text: "Stop"
                    color: "white"
                    font.pixelSize: 13
                }
            }
        }
    }

    component ToggleTile: AbstractButton {
        id: tile
        property string title
        property string subtitle
        property string iconName
        property color tint: "#3b82f6"
        property bool on: false
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        implicitHeight: 72
        hoverEnabled: true
        Accessible.name: title
        Accessible.description: subtitle
        background: Rectangle {
            radius: 16
            color: tile.hovered && tile.enabled ? t.raised : t.card
            border.color: tile.on ? Qt.rgba(tile.tint.r, tile.tint.g, tile.tint.b, 0.55) : "transparent"
            border.width: 1.5
            Behavior on border.color { ColorAnimation { duration: 200 } }
        }
        contentItem: RowLayout {
            spacing: 12
            opacity: tile.enabled ? 1 : 0.45
            Rectangle {
                width: 38; height: 38; radius: 19
                color: tile.on ? tile.tint : t.raised
                Behavior on color { ColorAnimation { duration: 200 } }
                IconImage {
                    anchors.centerIn: parent
                    source: win.iconUrl(tile.iconName)
                    sourceSize: Qt.size(20, 20)
                    color: tile.on ? "white" : t.muted
                    Behavior on color { ColorAnimation { duration: 200 } }
                }
            }
            ColumnLayout {
                spacing: 1
                Layout.fillWidth: true
                Label { text: tile.title; color: t.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                Label { text: tile.subtitle; color: t.muted; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
            }
        }
    }

    component MiniStep: AbstractButton {
        id: ms
        implicitWidth: 40
        implicitHeight: 40
        hoverEnabled: true
        background: Rectangle { radius: 20; color: ms.pressed ? t.track : ms.hovered ? t.raised : t.bg }
        contentItem: Label {
            text: ms.text
            color: t.text
            opacity: ms.enabled ? 1 : 0.3
            font.pixelSize: 20
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    component IconButton: AbstractButton {
        id: ib
        property url source
        implicitWidth: 36
        implicitHeight: 36
        hoverEnabled: true
        background: Rectangle { radius: 18; color: ib.pressed ? t.track : ib.hovered ? t.raised : "transparent" }
        contentItem: IconImage { source: ib.source; color: t.muted; sourceSize: Qt.size(20, 20); opacity: ib.enabled ? 1 : 0.4 }
    }

    component PowerSwitch: AbstractButton {
        id: ps
        enabled: ready
        implicitWidth: 64
        implicitHeight: 36
        hoverEnabled: true
        Accessible.name: "Power"
        Accessible.role: Accessible.CheckBox
        Accessible.checked: isOn
        onClicked: acModel.setPower(!isOn)
        background: Rectangle {
            radius: height / 2
            color: isOn ? win.accent : t.raised
            opacity: ps.enabled ? 1 : 0.5
            Behavior on color { ColorAnimation { duration: 200 } }
            Rectangle {
                width: 28; height: 28; radius: 14
                y: 4
                x: isOn ? parent.width - width - 4 : 4
                color: "white"
                Behavior on x { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                IconImage {
                    anchors.centerIn: parent
                    source: win.iconUrl("power")
                    sourceSize: Qt.size(16, 16)
                    color: isOn ? win.accent : t.off
                }
            }
        }
    }

    component StepButton: AbstractButton {
        id: sb
        property int direction
        implicitWidth: 52
        implicitHeight: 52
        enabled: canSetTemp
        autoRepeat: true
        hoverEnabled: true
        Accessible.name: direction < 0 ? "Lower temperature" : "Raise temperature"
        onClicked: acModel.nudge(direction)
        background: Rectangle {
            radius: width / 2
            color: sb.pressed ? t.track : sb.hovered ? t.raised : t.card
            border.color: t.track
            border.width: 1
        }
        contentItem: Label {
            text: sb.text
            color: t.text
            opacity: sb.enabled ? 1 : 0.3
            font.pixelSize: 26
            font.weight: Font.Light
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    component ModeChip: AbstractButton {
        id: chip
        property string mode
        readonly property bool selected: ready && ac.mode === mode
        readonly property color chipColor: modeColors[mode] || "#3b82f6"
        implicitHeight: 72
        enabled: isOn
        hoverEnabled: true
        Accessible.name: win.label(mode)
        Accessible.role: Accessible.RadioButton
        Accessible.checked: selected
        onClicked: acModel.setMode(mode)
        background: Rectangle {
            radius: 16
            color: chip.selected ? chip.chipColor : chip.hovered ? t.raised : t.card
            opacity: chip.enabled ? 1 : 0.55
            Behavior on color { ColorAnimation { duration: 200 } }
        }
        contentItem: ColumnLayout {
            spacing: 6
            opacity: chip.enabled ? 1 : 0.6
            IconImage {
                Layout.alignment: Qt.AlignHCenter
                source: win.iconUrl(modeIcons[chip.mode] || "mode-auto")
                sourceSize: Qt.size(24, 24)
                color: chip.selected ? "white" : chip.chipColor
                Behavior on color { ColorAnimation { duration: 200 } }
            }
            Label {
                Layout.alignment: Qt.AlignHCenter
                text: win.label(chip.mode)
                color: chip.selected ? "white" : t.text
                Behavior on color { ColorAnimation { duration: 200 } }
                font.pixelSize: 12
                font.weight: chip.selected ? Font.DemiBold : Font.Normal
            }
        }
    }

    component FanSlider: Slider {
        id: fs
        readonly property var speeds: ready ? ac.fan_speeds || [] : []
        readonly property string speed: speeds.length ? speeds[Math.round(value)] : ""
        from: 0
        to: Math.max(1, speeds.length - 1)
        stepSize: 1
        snapMode: Slider.SnapAlways
        enabled: isOn
        value: ready ? Math.max(0, speeds.indexOf(ac.fan_speed)) : 0
        onMoved: fanTimer.restart()
        Accessible.name: "Fan speed"
        Timer { id: fanTimer; interval: 350; onTriggered: acModel.setFanSpeed(fs.speed) }

        background: Item {
            x: fs.leftPadding
            y: fs.topPadding + fs.availableHeight / 2 - height / 2
            width: fs.availableWidth
            height: 8
            Rectangle { anchors.fill: parent; radius: 4; color: t.track }
            Rectangle { width: fs.visualPosition * parent.width; height: parent.height; radius: 4; color: win.accent }
            Repeater {
                model: fs.speeds.length
                Rectangle {
                    required property int index
                    width: 4; height: 4; radius: 2
                    x: (fs.speeds.length > 1 ? index / (fs.speeds.length - 1) : 0) * (parent.width - width)
                    anchors.verticalCenter: parent.verticalCenter
                    color: index <= fs.value ? Qt.rgba(1, 1, 1, 0.7) : t.muted
                    opacity: 0.6
                }
            }
        }
        handle: Rectangle {
            x: fs.leftPadding + fs.visualPosition * (fs.availableWidth - width)
            y: fs.topPadding + fs.availableHeight / 2 - height / 2
            width: 22; height: 22; radius: 11
            color: "white"
            border.color: win.accent
            border.width: 3
        }
    }

    component Field: TextField {
        Layout.fillWidth: true
        implicitHeight: 44
        color: t.text
        placeholderTextColor: t.muted
        font.pixelSize: 14
        leftPadding: 14
        background: Rectangle {
            radius: 12
            color: t.card
            border.color: parent.activeFocus ? "#3b82f6" : t.track
            border.width: parent.activeFocus ? 2 : 1
        }
    }

    component PillButton: AbstractButton {
        id: pb
        property bool primary: false
        implicitHeight: 44
        implicitWidth: Math.max(100, pbLabel.implicitWidth + 32)
        hoverEnabled: true
        background: Rectangle {
            radius: 22
            color: pb.primary ? (pb.pressed ? "#1d4ed8" : pb.hovered ? "#2563eb" : "#3b82f6") : (pb.hovered ? t.raised : t.card)
        }
        contentItem: Label {
            id: pbLabel
            text: pb.text
            color: pb.primary ? "white" : t.text
            font.pixelSize: 14
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }
}
