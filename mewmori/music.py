"""What Spotify is playing, over MPRIS.

MPRIS is the D-Bus interface every Linux media player implements, so this needs
no Spotify account, no API key and no polling: the player pushes a signal on
every track change, straight onto the GLib main loop the app already runs.
"""
from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

BUS = "org.mpris.MediaPlayer2.spotify"
PATH = "/org/mpris/MediaPlayer2"
IFACE = "org.mpris.MediaPlayer2.Player"


@dataclass(frozen=True)
class Track:
    id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""

    @property
    def is_ad(self) -> bool:
        """Spotify serves adverts through the same interface as music."""
        return "/ad/" in self.id or not self.artist

    def __str__(self):
        return f"{self.artist} — {self.title}" if self.artist else self.title


def _track(meta) -> Track:
    def one(key):
        v = meta.get(key)
        if isinstance(v, list):
            v = ", ".join(x for x in v if x)
        return (v or "").strip()

    return Track(
        id=one("mpris:trackid"),
        title=one("xesam:title"),
        artist=one("xesam:artist") or one("xesam:albumArtist"),
        album=one("xesam:album"),
    )


class Spotify:
    """Calls on_track(Track) whenever a different, real song starts.

    Survives Spotify not running yet, quitting, and being restarted — the bus
    name is watched rather than assumed.
    """

    def __init__(self, on_track, bus_name: str = BUS):
        self.on_track = on_track
        self.bus_name = bus_name
        self.current = Track()
        self.playing = False
        self.proxy = None
        self._watch = Gio.bus_watch_name(
            Gio.BusType.SESSION, bus_name, Gio.BusNameWatcherFlags.NONE,
            self._appeared, self._vanished,
        )

    # -- bus lifecycle --------------------------------------------------
    def _appeared(self, conn, name, _owner):
        try:
            self.proxy = Gio.DBusProxy.new_sync(
                conn, Gio.DBusProxyFlags.NONE, None, name, PATH, IFACE, None
            )
        except Exception:
            self.proxy = None
            return
        self.proxy.connect("g-properties-changed", lambda *_: self._refresh())
        self._refresh(first=True)

    def _vanished(self, _conn, _name):
        self.proxy, self.current, self.playing = None, Track(), False

    # -- state ----------------------------------------------------------
    def _get(self, prop):
        v = self.proxy.get_cached_property(prop) if self.proxy else None
        return v.unpack() if v is not None else None

    def _refresh(self, first=False):
        meta = self._get("Metadata")
        if meta is None:
            return
        self.playing = self._get("PlaybackStatus") == "Playing"
        track = _track(meta)
        if track.id == self.current.id:
            return
        self.current = track
        # the first read is whatever was already loaded — not news worth reacting to
        if not first and not track.is_ad and track.title:
            self.on_track(track)

    def stop(self):
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = None
