/*
 * The pages of the applet's settings dialog. One page is enough; the entry
 * has to exist all the same, or the dialog opens empty.
 */
import QtQuick
import org.kde.plasma.configuration

ConfigModel {
    ConfigCategory {
        name: "Лежанка"
        icon: "preferences-desktop-emoticons"
        source: "configGeneral.qml"
    }
}
