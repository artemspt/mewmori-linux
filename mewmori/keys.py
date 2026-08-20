"""Global hotkeys, and how a key is written down in a settings file.

Moved over from `speak`'s hotkeys.py plus the listener half of its main().
A key is stored as a string — "name:alt_r", "vk:269025095", "char:q" — because
plenty of real keys (media keys, backlight keys) have no pynput enum name and
would otherwise be unstorable.

pynput is an optional package, like the rest of the voice stack: without it
this module reports why and the cat runs with no hotkeys.
"""
from __future__ import annotations

import threading

PRESETS = {
    "name:alt_r": "Правый Alt", "name:alt": "Левый Alt",
    "name:ctrl_r": "Правый Ctrl", "name:ctrl": "Левый Ctrl",
    "name:shift_r": "Правый Shift", "name:shift": "Левый Shift",
    "name:cmd": "Super / Win", "name:f1": "F1",
}

_missing = ""


def _keyboard():
    global _missing
    try:
        from pynput import keyboard
        return keyboard
    except Exception as e:
        _missing = str(e)[:160]
        return None


def available() -> str:
    return "" if _keyboard() else f"нет pynput: {_missing}"


def key_to_id(key) -> str | None:
    keyboard = _keyboard()
    if keyboard is None:
        return None
    if isinstance(key, keyboard.Key):
        return f"name:{key.name}"
    if isinstance(key, keyboard.KeyCode):
        if key.vk is not None:
            return f"vk:{key.vk}"
        if key.char is not None:
            return f"char:{key.char}"
    return None


def id_to_key(key_id: str):
    keyboard = _keyboard()
    if keyboard is None or not key_id:
        return None
    if ":" not in key_id:
        key_id = f"name:{key_id}"
    kind, _, val = key_id.partition(":")
    if kind == "name":
        return getattr(keyboard.Key, val, None)
    if kind == "vk":
        try:
            return keyboard.KeyCode.from_vk(int(val))
        except ValueError:
            return None
    if kind == "char":
        return keyboard.KeyCode.from_char(val)
    return None


def label(key_id: str) -> str:
    if not key_id:
        return "?"
    if ":" not in key_id:
        key_id = f"name:{key_id}"
    if key_id in PRESETS:
        return PRESETS[key_id]
    kind, _, val = key_id.partition(":")
    return {"name": val, "char": f"'{val}'"}.get(kind, f"Клавиша #{val}")


def capture(timeout: float = 8.0) -> dict:
    """Wait for one key press and report what it was. For the settings window."""
    keyboard = _keyboard()
    if keyboard is None:
        return {"id": None, "label": None}
    found = {}

    def on_press(key):
        key_id = key_to_id(key)
        if key_id is None:
            return          # unrepresentable key: keep waiting
        found.update(id=key_id, label=label(key_id))
        return False

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    listener.join(timeout)
    if listener.is_alive():
        listener.stop()
    return found or {"id": None, "label": None}


class Hotkeys:
    """Watches the keyboard and calls back. Every callback runs on its thread.

    The suppression logic exists because the dictation key is usually a
    modifier: Alt alone starts a recording, and if the OS layout-switch
    shortcut (Alt+Shift) races it, whatever the microphone happened to pick up
    gets typed out as garbage. Any *other* modifier held together with the
    hotkey means this was not a deliberate press.
    """

    def __init__(self, hotkey_id, chord_ids, ask_id, handler, log=print):
        self.handler = handler          # object with the on_* methods below
        self.log = log
        self.listener = None
        keyboard = _keyboard()
        if keyboard is None:
            self.error = f"нет pynput: {_missing}"
            return
        self.error = ""
        self.hotkey_id = hotkey_id
        self.hotkey = id_to_key(hotkey_id) or keyboard.Key.alt_r
        self.ask_id = ask_id
        self.chord = {id_to_key(i) for i in (chord_ids or [])} - {None}
        modifiers = {keyboard.Key.shift, keyboard.Key.shift_r,
                     keyboard.Key.ctrl, keyboard.Key.ctrl_r,
                     keyboard.Key.alt, keyboard.Key.alt_r, keyboard.Key.alt_gr,
                     keyboard.Key.cmd, keyboard.Key.cmd_r}
        self.conflicts = modifiers - {self.hotkey}
        self.pressed = set()
        self.suppressed = False

    def start(self):
        if self.error:
            return
        keyboard = _keyboard()
        self.listener = keyboard.Listener(on_press=self._press,
                                          on_release=self._release)
        self.listener.daemon = True
        self.listener.start()

    def stop(self):
        if self.listener:
            self.listener.stop()
            self.listener = None

    def _chord_down(self):
        return bool(self.chord) and self.chord.issubset(self.pressed)

    def _press(self, key):
        try:
            already = key in self.pressed
            self.pressed.add(key)
            if self._chord_down():
                self.handler.on_chord_down()
                return
            if key_to_id(key) == self.hotkey_id or key == self.hotkey:
                # re-checked on every press, not only the first: a held
                # modifier auto-repeats, and gating on `already` would let a
                # later repeat through after the first was suppressed
                self.suppressed = bool(self.pressed & self.conflicts)
                if self.suppressed:
                    return
                self.handler.on_dictate_down()
                return
            if key in self.conflicts:
                self.handler.on_conflict()
                return
            if not already and key_to_id(key) == self.ask_id:
                self.handler.on_ask_down()
        except Exception as e:                  # a raise here kills the listener
            self.log(f"клавиши: {e}")

    def _release(self, key):
        try:
            was_chord = self._chord_down()
            self.pressed.discard(key)
            if was_chord and not self._chord_down():
                self.handler.on_chord_up()
                return
            if key_to_id(key) == self.hotkey_id or key == self.hotkey:
                if self.suppressed:
                    self.suppressed = False
                    return
                self.handler.on_dictate_up()
                return
            if key_to_id(key) == self.ask_id:
                self.handler.on_ask_up()
        except Exception as e:
            self.log(f"клавиши: {e}")
