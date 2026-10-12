// LG AC Desktop — main window. State and actions come from `acModel` (lg_ac/app.py).
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.impl
import QtQuick.Layouts
import QtQuick.Shapes

ApplicationWindow {
    id: win
    width: 400
    height: 600
    minimumWidth: 360
    minimumHeight: 580
    visible: true
    title: acModel.alias + (acModel.isDemo ? " (demo)" : "")
    color: t.bg

    // ---------- theme ----------
    // Follows the desktop's light/dark setting; LG_AC_THEME=dark|light overrides it.
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
    function iconUrl(name) { return iconDir + name + ".svg" }
    function label(v) { return ready && ac.labels[v] ? ac.labels[v] : v }
    function fmt(v) { return v === null || v === undefined ? "–" : String(Math.round(v * 2) / 2) }


    // ---------- main view ----------
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 18
        visible: !acModel.needsSetup

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
                        Layout.maximumWidth: 220
                    }
                    Rectangle {
                        visible: acModel.isDemo
                        radius: 6
                        color: t.raised
                        implicitWidth: demoLabel.implicitWidth + 12
                        implicitHeight: demoLabel.implicitHeight + 4
                        Label { id: demoLabel; anchors.centerIn: parent; text: "Demo"; color: t.muted; font.pixelSize: 11 }
                    }
                }
                Label {
                    text: ready ? "Room " + win.fmt(ac.current_temp) + "°" + ac.unit : " "
                    color: t.muted
                    font.pixelSize: 13
                }
            }
            IconButton { source: win.iconUrl("settings"); onClicked: acModel.openSetup(); Accessible.name: "Settings" }
            PowerSwitch {}
        }

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
                        text: !ready ? "" : !isOn ? "Off" : ac.can_set_temp ? "Target · °" + ac.unit : "No target in " + win.label(ac.mode)
                        color: t.muted
                        font.pixelSize: 13
                    }
                }
            }

            StepButton { text: "−"; direction: -1; anchors.verticalCenter: dial.verticalCenter; anchors.right: dial.left; anchors.rightMargin: -4 }
            StepButton { text: "+"; direction: 1; anchors.verticalCenter: dial.verticalCenter; anchors.left: dial.right; anchors.leftMargin: -4 }
        }

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

        Item { Layout.fillHeight: true }

        // Status
        RowLayout {
            Layout.fillWidth: true
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

    // ---------- components ----------

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
