/*
 * The settings page for the basket.
 *
 * Plasma binds each control to a `cfg_<name>` property it creates from
 * config/main.xml — the names must match exactly, and the dialog's Apply
 * button writes them back on its own. There is no save handler to write here.
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

Kirigami.FormLayout {
    id: page

    property alias cfg_scale: scale.value
    property alias cfg_pollInterval: poll.value
    property alias cfg_launchOnClick: launch.checked
    property alias cfg_catCommand: command.text
    property alias cfg_dimWhenAbsent: dim.checked

    RowLayout {
        Kirigami.FormData.label: "Размер:"
        QQC2.Slider {
            id: scale
            from: 0.5
            to: 1.0
            stepSize: 0.05
            implicitWidth: Kirigami.Units.gridUnit * 10
        }
        QQC2.Label {
            text: Math.round(scale.value * 100) + "%"
        }
    }

    QQC2.SpinBox {
        id: poll
        Kirigami.FormData.label: "Опрос кота, мс:"
        from: 250
        to: 10000
        stepSize: 250
    }

    QQC2.Label {
        text: "Реже — меньше запусков `cat` в фоне,\nмедленнее реакция на засыпание."
        font: Kirigami.Theme.smallFont
        opacity: 0.7
    }

    Item { Kirigami.FormData.isSection: true }

    QQC2.CheckBox {
        id: dim
        Kirigami.FormData.label: "Когда кота нет:"
        text: "притушить лежанку"
    }

    QQC2.CheckBox {
        id: launch
        text: "запускать кота по клику"
    }

    QQC2.TextField {
        id: command
        Kirigami.FormData.label: "Чем запускать:"
        enabled: launch.checked
        placeholderText: "~/PycharmProjects/mewmori-linux/run.sh --bed"
        implicitWidth: Kirigami.Units.gridUnit * 18
    }

    QQC2.Label {
        text: "Пусто — лежанка возьмёт путь из файла,\nкоторый кот пишет при запуске."
        font: Kirigami.Theme.smallFont
        opacity: 0.7
    }
}
