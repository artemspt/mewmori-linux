/*
 * Mewmori bed — a basket that lives in the panel.
 *
 * The cat is a separate GTK process, so the two meet over D-Bus:
 *   - this applet reports where on screen it drew itself, which is what lets
 *     the cat notice it has been dragged onto the basket;
 *   - a click asks the cat to go to sleep, or wakes it again.
 *
 * The state comes back through a small file rather than a D-Bus signal, since
 * QML cannot subscribe to signals without a C++ plugin. It is read with `cat`
 * through the executable engine: QML blocks XMLHttpRequest on local files
 * unless QML_XHR_ALLOW_FILE_READ is set, which cannot be relied upon inside
 * plasmashell.
 */
import QtCore
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root

    readonly property string service: "org.mewmori.Cat"
    readonly property string objPath: "/org/mewmori/Cat"
    readonly property string statePath:
        String(StandardPaths.writableLocation(StandardPaths.GenericCacheLocation))
            .replace(/^file:\/\//, "") + "/mewmori/state"

    readonly property string launchPath:
        String(StandardPaths.writableLocation(StandardPaths.GenericCacheLocation))
            .replace(/^file:\/\//, "") + "/mewmori/launch"

    property bool occupied: false
    property bool catAlive: false

    preferredRepresentation: compactRepresentation
    toolTipMainText: "Лежанка Мяумори"
    toolTipSubText: !catAlive ? (Plasmoid.configuration.launchOnClick
                                 ? "Кот не запущен — нажмите, чтобы запустить"
                                 : "Кот не запущен")
                              : occupied ? "Кот спит — нажмите, чтобы разбудить"
                                         : "Перетащите сюда кота с рабочего стола"

    // -- talking to the cat ------------------------------------------------
    P5Support.DataSource {
        id: sh
        engine: "executable"
        connectedSources: []
        onNewData: function (source, data) {
            disconnectSource(source)
        }
        function call(method, args) {
            var cmd = "dbus-send --session --type=method_call"
                    + " --dest=" + root.service + " " + root.objPath
                    + " " + root.service + "." + method
            for (var i = 0; i < args.length; i++)
                cmd += " int32:" + args[i]
            connectSource(cmd)
        }

        // The applet has no idea where the repository was cloned, so the path
        // comes from the file the cat writes on its first run. An explicit
        // command in the settings wins over it.
        function launchCat() {
            var custom = String(Plasmoid.configuration.catCommand || "").trim()
            if (custom.length > 0) {
                connectSource(custom)
                return
            }
            connectSource("sh -c 'exec \"$(cat " + root.launchPath + ")\" --bed'")
        }
    }

    P5Support.DataSource {
        id: poll
        engine: "executable"
        connectedSources: []
        onNewData: function (source, data) {
            var out = String(data.stdout || "").trim()
            root.catAlive = out.length > 0
            root.occupied = out === "1"
            disconnectSource(source)
        }
    }

    Timer {
        interval: Math.max(250, Plasmoid.configuration.pollInterval || 1000)
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: poll.connectSource("cat " + root.statePath)
    }

    // -- the sprite, used for both representations -------------------------
    Component {
        id: basket

        Item {
            id: view

            // matches the baked sprite, so the basket never letterboxes
            readonly property real aspect: 1.244
            readonly property bool horizontal:
                Plasmoid.formFactor === PlasmaCore.Types.Horizontal
            // the applet still occupies the full panel thickness; the sprite
            // inside it shrinks, which is what a size setting has to mean in a
            // panel that decides its own height
            // the fallback is not belt and braces: a setting that has never
            // been written reads back undefined, and Math.min(1, undefined) is
            // NaN, which collapses the sprite to nothing
            readonly property real fill:
                Math.max(0.3, Math.min(1.0, Plasmoid.configuration.scale || 1.0))
            Layout.minimumWidth: horizontal ? height * aspect : 22
            Layout.minimumHeight: horizontal ? 22 : width / aspect
            Layout.preferredWidth: horizontal ? height * aspect : width
            Layout.preferredHeight: horizontal ? height : width / aspect

            function report() {
                if (view.width < 4 || view.height < 4)
                    return
                var p = view.mapToGlobal(0, 0)
                sh.call("SetBedRect", [Math.round(p.x), Math.round(p.y),
                                       Math.round(view.width), Math.round(view.height)])
            }

            Image {
                anchors.centerIn: parent
                width: parent.width * view.fill
                height: parent.height * view.fill
                source: Qt.resolvedUrl(root.occupied ? "../images/bed_sleeping.png"
                                                     : "../images/bed_empty.png")
                fillMode: Image.PreserveAspectFit
                smooth: false          // pixel art: never interpolate
                mipmap: false
                // the sprites are re-baked in place under the same names, and
                // plasmashell would otherwise keep serving the old pixmap
                cache: false
                opacity: root.catAlive || !Plasmoid.configuration.dimWhenAbsent
                         ? 1.0 : 0.4
                Behavior on opacity { NumberAnimation { duration: 150 } }
            }

            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    view.report()
                    if (!root.catAlive) {
                        // nothing to wake: start one, if that is wanted
                        if (Plasmoid.configuration.launchOnClick)
                            sh.launchCat()
                        return
                    }
                    sh.call(root.occupied ? "WakeUp" : "PutToBed", [])
                }
            }

            // the panel moves, resizes and re-lays-out; keep the cat informed
            Timer {
                interval: 2000
                running: true
                repeat: true
                triggeredOnStart: true
                onTriggered: view.report()
            }

            onWidthChanged: report()
            onHeightChanged: report()
            Component.onCompleted: report()
        }
    }

    compactRepresentation: basket
    fullRepresentation: basket
}
