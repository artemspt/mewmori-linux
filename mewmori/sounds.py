"""A tiny click that plays while the cat types.

Fire-and-forget by design: every blip is an external player process
(paplay, falling back to aplay), so nothing here can block the GTK loop
or take the cat down — if no player exists, or the file is missing, the
sound is simply skipped. Calls are throttled because tokens can arrive
faster than the ear could tell apart anyway.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

SOUND = Path(__file__).resolve().parent.parent / "assets" / "sounds" / "type.wav"
MIN_GAP = 0.04        # s between blips; matches the ~31 ms per typed character

_player: str | None = None   # resolved once: path to a player, or "" for none
_last = 0.0                  # monotonic ts of the last blip actually started


def _resolve() -> str:
    global _player
    if _player is None:
        # paplay goes through pulse/pipewire (volume, per-app control);
        # aplay is the bare ALSA fallback when pulseaudio isn't there
        _player = shutil.which("paplay") or shutil.which("aplay") or ""
    return _player


def play_type_sound(min_gap: float = MIN_GAP) -> bool:
    """Start playing the typing blip unless one began within min_gap."""
    global _last
    now = time.monotonic()
    if now - _last < min_gap:
        return False
    player = _resolve()
    if not player or not SOUND.is_file():
        return False
    try:
        # Popen, not run(): the process plays while the cat keeps typing,
        # and its output is discarded so it cannot pollute the terminal
        subprocess.Popen([player, str(SOUND)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    _last = now
    return True
