"""Everything a person might want to change, in one JSON file.

Kept deliberately flat and small: a setting earns its place here only if
someone would plausibly want it different, and everything else stays a
constant in the code where it belongs. Unknown keys in the file are preserved
rather than dropped, so a newer version's settings survive an older one.

No GTK — prefs.py is the window, this is only the store, and the modules that
read settings must keep working without a display.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mewmori"
PATH = CONFIG / "settings.json"

DEFAULTS = {
    # who the cat is talking to — Russian conjugates past-tense verbs by
    # gender, so guessing this wrong is audible in every other sentence
    "owner": "",
    "owner_gender": "мужской",
    # looks
    "skin": "classic_cat",
    "height": 120,
    # one model for everything: empty means "work it out from what ollama has"
    "model": "",
    # what the cat is allowed to notice
    "watch_screen": True,
    "watch_tabs": True,
    "watch_notifications": True,
    "watch_claude": True,
    "watch_hardware": True,
    "watch_music": True,
    "watch_telegram": False,
    # say out loud what the glance at the screen found, not only write it down
    "comment_screen": True,
    # once in a while the cat vanishes for a couple of minutes and comes back
    # wearing a random cosmetic; average gap 5-7 min as requested
    "vanish_enabled": True,
    "vanish_interval_min": 6,
    # how often it may speak unprompted, in seconds between remarks. 75 turned
    # out to be too quiet: every source shares this one floor, so music, tabs,
    # programs and the screen all queued behind each other
    "chatter_gap": 40.0,
    # carry the conversation across restarts
    "remember_session": True,
    # a soft click while the cat types; paplay/aplay must exist for it to sound
    "type_sound": True,
    # ask before sending anything to another human
    "telegram_confirm": True,
    # hearing "да"/"нет". Empty means: borrow whatever the `speak` checkout
    # next door already has, and fall back to a window with two buttons.
    "voice_confirm": True,
    "voice_python": "",
    "voice_model": "",
    # -- the voice stack moved over from `speak` --------------------------
    "voice_enabled": False,     # off until the packages are actually there
    "voice_input": "pulse",     # raw ALSA devices refuse a fixed 16 kHz
    "voice_device": "cuda",
    "voice_compute": "float16",
    "voice_language": "ru",
    # hold to dictate, release to stop. Two taps in a row fix the field instead
    "dictate_key": "name:alt_r",
    "dictate_mode": "push",     # or "toggle"
    "dictate_live": True,       # type words as they are recognised
    "command_chord": ["name:shift", "name:ctrl"],
    "ask_key": "name:f1",
    # the cat answers to any of these, as whole words
    "wake_word_enabled": True,
    "wake_words": ["мяумори", "мяуми", "кот"],
}

_cache: dict | None = None


def load(reload: bool = False) -> dict:
    global _cache
    if _cache is not None and not reload:
        return _cache
    data = dict(DEFAULTS)
    try:
        stored = json.loads(PATH.read_text(encoding="utf8"))
        if isinstance(stored, dict):
            data.update(stored)
    except (OSError, json.JSONDecodeError):
        pass
    _cache = data
    return data


def get(key: str, default=None):
    return load().get(key, DEFAULTS.get(key, default))


def save(values: dict) -> None:
    """Merge and write. Whole-file replace, so a crash cannot half-write it."""
    data = load()
    data.update(values)
    try:
        CONFIG.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
        os.replace(tmp, PATH)
    except OSError:
        pass


def owner() -> str:
    """The environment still wins, so a one-off run can pretend to be someone else."""
    return os.environ.get("MEWMORI_OWNER") or get("owner") or "Хозяин"


def owner_gender() -> str:
    return os.environ.get("MEWMORI_OWNER_GENDER") or get("owner_gender") or "мужской"
