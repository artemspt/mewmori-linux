#!/bin/bash
# Put the basket into the panel's applet directory and make plasmashell notice.
#
# The applet is a plain KPackage — a directory of QML — so installing it is a
# copy. kpackagetool6 is preferred because it also refreshes the applet cache;
# without that, plasmashell keeps serving the version it saw at login.
#
# It is given an absolute path and its result is checked: passing a bare applet
# id makes --upgrade remove the installed copy and then fail to put anything
# back, which leaves the panel with a widget it can no longer find.
set -e
HERE="$(dirname "$(readlink -f "$0")")"

APPLET=com.mewmori.bed
SRC="$HERE/$APPLET"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/plasma/plasmoids/$APPLET"

if command -v kpackagetool6 >/dev/null; then
    if [ -d "$DEST" ]; then
        kpackagetool6 --type Plasma/Applet --upgrade "$SRC" || true
    else
        kpackagetool6 --type Plasma/Applet --install "$SRC" || true
    fi
fi

if [ ! -f "$DEST/metadata.json" ]; then
    echo "kpackagetool6 не справился — копирую вручную"
    mkdir -p "$(dirname "$DEST")"
    rm -rf "$DEST"
    cp -r "$SRC" "$DEST"
fi

test -f "$DEST/contents/ui/main.qml" || { echo "УСТАНОВКА НЕ УДАЛАСЬ"; exit 1; }

echo
echo "Установлено в $DEST"
echo "Добавьте «Лежанка Мяумори» в панель: правый клик по панели → Добавить виджеты."
echo "Если виджет уже стоял — перезапустите оболочку, иначе останется старая версия:"
echo "    kquitapp6 plasmashell && kstart plasmashell"
