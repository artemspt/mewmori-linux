"""The bed lives in the Plasma panel as a QML applet; this is the cat's half.

The applet and the cat are separate processes, so they meet over D-Bus:
  * the applet calls SetBedRect to say where on screen it drew itself, which is
    what lets the cat notice it has been dragged onto the basket;
  * WakeUp / PutToBed are the applet's click;
  * the sleeping state goes back the other way through a small file, because
    QML cannot subscribe to D-Bus signals without a C++ plugin.

With no applet in the panel, rect stays None, contains() is always False, and
the cat simply carries on living on the desktop.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS = "org.mewmori.Cat"
PATH = "/org/mewmori/Cat"
CATCH = 1.7      # the drop zone is bigger than the sprite: it is a tiny target
STALE = 12.0     # s without word from the applet before it counts as gone

XML = """
<node>
  <interface name='org.mewmori.Cat'>
    <method name='SetBedRect'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
      <arg type='i' name='w' direction='in'/>
      <arg type='i' name='h' direction='in'/>
    </method>
    <method name='WakeUp'/>
    <method name='PutToBed'/>
  </interface>
</node>
"""


def state_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "mewmori" / "state"


def remember_launcher() -> None:
    """Leave the path to run.sh where the applet can find it.

    The basket can start the cat when it is not running, but an applet has no
    idea where anyone cloned the repository. Rather than make that a setting
    everyone has to fill in, the cat writes down its own launcher the first
    time it runs, and the applet reads that.
    """
    launcher = Path(__file__).resolve().parent.parent / "run.sh"
    try:
        if launcher.exists():
            path = state_path().with_name("launch")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(launcher), encoding="utf8")
    except OSError:
        pass


class PanelBed:
    def __init__(self, on_wake, on_sleep):
        self.on_wake = on_wake
        self.on_sleep = on_sleep
        self.rect = None          # (x, y, w, h) in screen pixels
        self.seen_at = 0.0
        self.occupied = False
        self.file = state_path()
        self._write()
        remember_launcher()
        self._own = Gio.bus_own_name(
            Gio.BusType.SESSION, BUS, Gio.BusNameOwnerFlags.REPLACE,
            self._acquired, None, None,
        )

    # -- bus ------------------------------------------------------------
    def _acquired(self, conn, _name):
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        conn.register_object(PATH, info, self._call, None, None)

    def _call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "SetBedRect":
            x, y, w, h = params.unpack()
            if w > 0 and h > 0:
                self.rect = (x, y, w, h)
                self.seen_at = time.monotonic()
        elif method == "WakeUp":
            GLib.idle_add(self.on_wake)
        elif method == "PutToBed":
            GLib.idle_add(self.on_sleep)
        invocation.return_value(None)

    # -- state ----------------------------------------------------------
    @property
    def present(self) -> bool:
        """Is the applet actually sitting in a panel right now?"""
        return self.rect is not None and time.monotonic() - self.seen_at < STALE

    def contains(self, sx, sy) -> bool:
        if not self.present:
            return False
        x, y, w, h = self.rect
        cx, cy = x + w / 2.0, y + h / 2.0
        rx, ry = w * CATCH / 2.0, h * CATCH / 2.0
        return ((sx - cx) / rx) ** 2 + ((sy - cy) / ry) ** 2 <= 1.0

    def slot(self):
        """Where on screen the cat should reappear when it wakes."""
        if not self.rect:
            return None
        x, y, w, h = self.rect
        return x + w / 2.0, y + h + 40.0

    def set_occupied(self, on: bool):
        if self.occupied != on:
            self.occupied = on
            self._write()

    def set_hover(self, _on):
        """The applet draws its own hover; nothing to do on this side."""

    def _write(self):
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text("1" if self.occupied else "0")
        except OSError:
            pass

    def close(self):
        """Blank the state so the applet greys out once the cat is gone."""
        try:
            self.file.write_text("")
        except OSError:
            pass
