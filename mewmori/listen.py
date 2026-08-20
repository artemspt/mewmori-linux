"""The voice state machine, with the cat as its face.

This is `speak`'s Dictator, rehomed. The behaviour is the same — hold a key to
dictate, tap it twice to fix the field, hold a chord or call the cat by name to
give a command — and the difference is what the owner sees while it happens:
instead of a desktop notification, the cat perks its ears, and whatever is
being recognised appears in its speech balloon as it is spoken.

One thing genuinely changed rather than moved. `speak`'s F1 question went to a
generic assistant prompt; here it goes to the cat, through the same character
and the same journal it uses for everything else. Asking out loud and asking in
the little text window are now the same conversation.

Every callback below arrives on a pynput or audio thread. Nothing here touches
GTK directly — it all goes through GLib.idle_add.
"""
from __future__ import annotations

import sys
import threading
import time

from gi.repository import GLib

from . import commands, config, ears, keys

TAP = 0.3               # s: shorter than this is a tap, not a hold
DOUBLE_TAP = 0.4        # s within which a second tap means "fix the field"
STREAM_EVERY = 0.7      # s between live re-transcriptions while dictating
MIN_CHUNK = 0.5         # s of audio before a partial pass is worth doing


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


class Listener:
    """Owns the ears, the hotkeys, and what the cat does about them."""

    def __init__(self, cat):
        self.cat = cat
        self.busy = False           # a transcription is in flight
        self.recording = False
        self.chord_recording = False
        self.ask_recording = False
        self.key_down = False
        self.pressed_at = 0.0
        self.last_tap = None
        self.started_at = 0.0
        self.shown = ""             # what live typing has put on screen so far
        self.live = False
        self._stop_stream = threading.Event()
        self._stream_thread = None

        self.rec = ears.Recorder()
        self.chord_rec = ears.Recorder()
        self.ask_rec = ears.Recorder()

        self.ears = ears.Ears(on_wake=self._on_wake,
                              should_pause=self._is_busy, log=log)
        self.keys = keys.Hotkeys(
            config.get("dictate_key"), config.get("command_chord"),
            config.get("ask_key"), self, log=log)
        self.error = self.keys.error
        self.keys.start()

    def _is_busy(self):
        return (self.busy or self.recording or self.chord_recording
                or self.ask_recording)

    def stop(self):
        self.keys.stop()
        self.ears.stop_wake()

    # -- what the owner sees ------------------------------------------------
    def _say(self, text, secs=6.0):
        GLib.idle_add(self.cat.say, text, secs)

    def _perk(self):
        """Ears up and stop wandering: the cat is listening now."""
        def go():
            self.cat.anim.react("cursorNear")
            self.cat.anim.set_state("interact")
            self.cat.target, self.cat.speed = None, 0.0
            self.cat.listening = True
            return False
        GLib.idle_add(go)

    def _settle(self):
        def go():
            self.cat.listening = False
            self.cat.plan_in = 2.0
            return False
        GLib.idle_add(go)

    # -- dictation ----------------------------------------------------------
    def on_dictate_down(self):
        if self.key_down:
            return                      # OS key-repeat, not a new press
        self.key_down = True
        self.pressed_at = time.time()

        if self.last_tap is not None and self.pressed_at - self.last_tap < DOUBLE_TAP:
            self.last_tap = None
            threading.Thread(target=self._correct, daemon=True).start()
            return

        if config.get("dictate_mode") == "toggle" and self.recording:
            self._stop_dictation()
        else:
            self._start_dictation()

    def on_dictate_up(self):
        self.key_down = False
        held = time.time() - self.pressed_at
        self.last_tap = time.time() if held < TAP else None
        if config.get("dictate_mode") != "toggle":
            self._stop_dictation()

    def on_conflict(self):
        """Another modifier joined the hotkey — a layout switch, not dictation.

        Whatever the microphone caught in the meantime (background music
        included) must not be typed out, and anything already typed live has
        to be taken back off the screen.
        """
        if not self.recording:
            return
        self.recording = False
        self._stop_stream.set()
        self.rec.stop()
        if self.shown:
            commands.backspace(len(self.shown))
            self.shown = ""
        self._settle()
        self._say("не разобрал, отставить")

    def _start_dictation(self):
        if self.recording or self.busy:
            return
        try:
            self.rec.start()
        except Exception as e:
            self._say(f"микрофон занят: {str(e)[:40]}")
            return
        self.recording = True
        self.started_at = time.time()
        self.shown = ""
        self._perk()
        # live typing only makes sense with somewhere to type; with nothing
        # focused there is nothing to backspace into
        self.live = bool(config.get("dictate_live")) and commands.has_active_window()
        if self.live:
            self._stop_stream.clear()
            self._stream_thread = threading.Thread(target=self._stream, daemon=True)
            self._stream_thread.start()

    def _stop_dictation(self):
        if not self.recording:
            return
        self.recording = False
        self._stop_stream.set()
        audio = self.rec.stop()
        if self._stream_thread:
            self._stream_thread.join(timeout=2)
            self._stream_thread = None
        self._settle()
        if time.time() - self.started_at < ears.MIN_SECONDS or audio.size == 0:
            return
        threading.Thread(target=self._finish, args=(audio,), daemon=True).start()

    def _stream(self):
        """Re-transcribe the buffer as it grows, so words appear while spoken."""
        while not self._stop_stream.wait(STREAM_EVERY):
            audio = self.rec.snapshot()
            if audio.size < ears.SAMPLE_RATE * MIN_CHUNK:
                continue
            text = self.ears.transcribe(audio, beam_size=1,
                                        language=config.get("voice_language"))
            if text:
                self._patch(text)

    def _patch(self, new: str):
        """Backspace only as far back as the two versions differ, then retype."""
        old = self.shown
        if new == old:
            return
        common, limit = 0, min(len(old), len(new))
        while common < limit and old[common] == new[common]:
            common += 1
        commands.backspace(len(old) - common)
        if new[common:]:
            commands.type_text(new[common:])
        self.shown = new
        self._say(f"пишу: {new[-60:]}", secs=4)

    def _finish(self, audio):
        self.busy = True
        try:
            # a wider beam here: it only has to redo the tail of what the live
            # pass already typed, not the whole sentence
            text = self.ears.transcribe(audio, beam_size=5,
                                        language=config.get("voice_language"))
            if not text:
                self._say("не расслышал")
                return
            if self.live:
                self._patch(text)
            else:
                commands.type_text(text)
            self._say(f"«{text[:70]}»", secs=5)
        finally:
            self.busy = False

    # -- fixing what was typed ---------------------------------------------
    def _correct(self):
        if self.busy:
            return
        self.busy = True
        self._say("сейчас поправлю…", secs=20)

        def done(fixed, err):
            self.busy = False
            self._say(f"поправил: {fixed[:60]}" if not err else f"не вышло: {err}")

        commands.correct(self.cat.model, done)

    # -- commands ----------------------------------------------------------
    def on_chord_down(self):
        if self.chord_recording or self.recording:
            return
        try:
            self.chord_rec.start()
        except Exception:
            return
        self.chord_recording = True
        self.started_at = time.time()
        self._perk()

    def on_chord_up(self):
        if not self.chord_recording:
            return
        self.chord_recording = False
        audio = self.chord_rec.stop()
        self._settle()
        if time.time() - self.started_at < ears.MIN_SECONDS or audio.size == 0:
            return
        threading.Thread(target=self._command, args=(audio,), daemon=True).start()

    def _on_wake(self, audio, tail):
        """Called by name. If the command came in the same breath, use it."""
        if self._is_busy():
            return
        self._perk()
        if tail:
            threading.Thread(target=self._command, args=(audio,),
                             daemon=True).start()
            return
        self._say("мур?", secs=8)
        heard = self.ears.listen_until_quiet()
        if heard.size < ears.SAMPLE_RATE * ears.MIN_SECONDS:
            self._settle()
            return
        threading.Thread(target=self._command, args=(heard,), daemon=True).start()

    def _command(self, audio):
        if self.busy:
            return
        self.busy = True
        try:
            # language is pinned: autodetect is unreliable on short phrases,
            # and commands are always Russian
            text = self.ears.transcribe(audio, beam_size=5, language="ru",
                                        hotwords=ears.HOTWORDS)
            actions = commands.match(text)
            if not actions:
                # not a command — so it was a question, and the cat answers it
                # in its own voice rather than saying "не распознано"
                if text.strip():
                    GLib.idle_add(self.cat.ask, text.strip(), False, False, True)
                else:
                    self._say("не расслышал")
                return
            done = [a for a in actions if commands.run(a)]
            if done:
                self._say(", ".join(commands.describe(a) for a in done), secs=6)
            else:
                self._say("не получилось")
        finally:
            self.busy = False
            self._settle()

    # -- asking out loud ----------------------------------------------------
    def on_ask_down(self):
        if self._is_busy():
            return
        try:
            self.ask_rec.start()
        except Exception:
            return
        self.ask_recording = True
        self.started_at = time.time()
        self._perk()

    def on_ask_up(self):
        if not self.ask_recording:
            return
        self.ask_recording = False
        audio = self.ask_rec.stop()
        held = time.time() - self.started_at
        self._settle()
        if held < TAP:
            GLib.idle_add(self.cat._prompt_window)   # a tap: type it instead
            return
        if audio.size < ears.SAMPLE_RATE * ears.MIN_SECONDS:
            return
        threading.Thread(target=self._ask, args=(audio,), daemon=True).start()

    def _ask(self, audio):
        if self.busy:
            return
        self.busy = True
        try:
            text = self.ears.transcribe(audio, beam_size=5, language="ru")
            if not text.strip():
                self._say("не расслышал")
                return
            # straight into the cat's own conversation: same character, same
            # history, same journal — asking out loud is not a separate mode
            GLib.idle_add(self.cat.ask, text.strip(), False, False, True)
        finally:
            self.busy = False
