"""The cat's hearing: microphone, Whisper, and the wake word.

Moved over from the `speak` daemon, with one architectural change: the model
is loaded **once, in this process, and kept warm**. The daemon could afford a
cold start; a cat that has just been asked a question cannot, and a fresh
interpreter per phrase cost about six seconds of it.

Everything that touches the GPU goes through one lock. This is not caution:
dictation, the wake word and a voice answer all decode on the same CTranslate2
context from different threads, and two overlapping `transcribe()` calls on it
crash the process with "CUDA failed with error unspecified launch failure".
The busy flags upstream stop things from *starting* together; only the mutex
stops them from *overlapping*.

Unlike the rest of the cat, this module needs real packages — sounddevice,
numpy and faster-whisper. It is imported lazily and reports what is missing
rather than taking the cat down with it.
"""
from __future__ import annotations

import os
import re
import threading
import time

from . import config

SAMPLE_RATE = 16000
MIN_SECONDS = 0.3        # anything shorter than this is a slip of the finger
WAKE_CHECK = 1.0         # s between passes over the rolling wake-word buffer
WAKE_BUFFER = 2.5        # s of audio kept for the wake word to look at
COMMAND_MAX = 6.0        # s a spoken command may run before it is cut off
COMMAND_SILENCE_MS = 900  # trailing quiet that means the sentence ended

# Whisper "corrects" program names into unrelated real Russian words unless it
# is nudged: PyCharm becomes "пайчару", CLion becomes "Каллеон".
HOTWORDS = ("PyCharm CLion Claude Telegram Spotify Firefox Konsole Dolphin "
            "Мяумори пайчарм клион клод телеграм спотифай")

_missing = ""


def wake_pattern():
    """Any of the names the cat answers to, matched as whole words.

    Whole words matter more than it looks: "кот" as a substring fires on
    "который", "скотч" and "работа" — the cat would answer to half the
    sentences in the room.
    """
    words = config.get("wake_words") or ["мяумори"]
    if isinstance(words, str):
        words = [words]
    names = [re.escape(w.strip().lower()) for w in words if w and w.strip()]
    if not names:
        names = ["мяумори"]
    # a Russian name gets case endings — "кота", "мяумори," — so a short
    # suffix is allowed after the stem, but nothing before it
    return re.compile(r"\b(" + "|".join(names) + r")(?:[аеиуыоюя]|ом|ик)?\b")


def _load():
    """Import the heavy packages once, and remember why if they are absent."""
    global _missing
    try:
        import numpy  # noqa: F401
        import sounddevice  # noqa: F401
        from faster_whisper import WhisperModel  # noqa: F401
    except Exception as e:                    # ImportError, PortAudio, ...
        _missing = str(e)[:160]
        return False
    return True


def available() -> str:
    """Empty when the cat can hear; otherwise what is missing."""
    if _load():
        return ""
    return f"нет голосового стека: {_missing}"


def model_path() -> str:
    from . import voice
    return voice.model_path()


class Recorder:
    """Holds an open input stream and hands out what it has heard so far."""

    def __init__(self, device=""):
        self.device = device or config.get("voice_input") or "pulse"
        self._frames = []
        self._lock = threading.Lock()
        self._stream = None

    def start(self):
        import sounddevice as sd
        self._frames = []
        # through pulse/pipewire rather than a raw ALSA device: hardware
        # inputs often refuse a fixed 16 kHz outright, where the sound server
        # simply resamples — and it follows whatever input the owner picked
        self._stream = sd.InputStream(device=self.device, samplerate=SAMPLE_RATE,
                                      channels=1, dtype="float32",
                                      callback=self._callback)
        self._stream.start()

    def _callback(self, indata, _frames, _time, _status):
        with self._lock:
            self._frames.append(indata.copy())

    def snapshot(self):
        import numpy as np
        with self._lock:
            frames = list(self._frames)
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames, axis=0).reshape(-1)

    def stop(self):
        if self._stream is None:
            return self.snapshot()
        self._stream.stop()
        self._stream.close()
        self._stream = None
        return self.snapshot()


class Ears:
    """One warm Whisper model, and everything that listens through it."""

    def __init__(self, on_wake=None, should_pause=lambda: False, log=print):
        self.error = ""
        self.model = None
        self.on_wake = on_wake
        self.should_pause = should_pause
        self.log = log
        self.gpu_lock = threading.Lock()
        self._wake_stop = threading.Event()
        self._wake_thread = None
        self.ready = threading.Event()
        threading.Thread(target=self._load_model, daemon=True).start()

    def _load_model(self):
        """Loading takes seconds and must not hold up the first frame."""
        if not _load():
            self.error = _missing
            self.ready.set()
            return
        from faster_whisper import WhisperModel
        path = model_path()
        device = config.get("voice_device") or "cuda"
        compute = config.get("voice_compute") or "float16"
        try:
            self.model = WhisperModel(path, device=device, compute_type=compute)
        except Exception as e:
            self.log(f"whisper на {device} не поднялся ({str(e)[:80]}), пробую CPU")
            try:
                self.model = WhisperModel(path, device="cpu", compute_type="int8")
            except Exception as e2:
                self.error = str(e2)[:160]
        self.ready.set()
        if self.model and config.get("wake_word_enabled"):
            self.start_wake()

    def _fatal_if_cuda(self, e: Exception) -> None:
        """A poisoned CUDA context does not crash anything — it just makes
        every later transcription fail forever, with no symptom except that
        the cat stops hearing. Better to die and be restarted."""
        if "CUDA" in str(e):
            self.log(f"фатальная ошибка GPU, выхожу: {e}")
            os._exit(1)

    def transcribe(self, audio, beam_size: int = 5, language: str = "ru",
                   hotwords: str | None = None, vad: bool = True) -> str:
        if self.model is None:
            return ""
        # transcribe() only builds a generator; the GPU work happens while the
        # segments are iterated, so the lock has to cover the loop as well
        with self.gpu_lock:
            try:
                segments, _info = self.model.transcribe(
                    audio, language=language or None, vad_filter=vad,
                    beam_size=beam_size, hotwords=hotwords,
                    # keeps one misheard segment from biasing the next
                    condition_on_previous_text=False)
                return "".join(s.text for s in segments).strip()
            except RuntimeError as e:
                self._fatal_if_cuda(e)
                return ""
            except Exception:
                return ""

    # -- the wake word -----------------------------------------------------
    def start_wake(self):
        if self._wake_thread and self._wake_thread.is_alive():
            return
        self._wake_stop.clear()
        self._wake_thread = threading.Thread(target=self._wake_loop, daemon=True)
        self._wake_thread.start()

    def stop_wake(self):
        self._wake_stop.set()

    def _wake_loop(self):
        """A cheap VAD runs continuously; Whisper only when speech was heard.

        Without the VAD gate this would spend the GPU on silence all day.
        """
        import numpy as np
        import sounddevice as sd
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        frames, lock = [], threading.Lock()

        def cb(indata, *_):
            with lock:
                frames.append(indata.copy())
                cap = int(WAKE_BUFFER * SAMPLE_RATE)
                total = sum(f.shape[0] for f in frames)
                while total > cap and len(frames) > 1:
                    total -= frames[0].shape[0]
                    frames.pop(0)

        device = config.get("voice_input") or "pulse"
        try:
            stream = sd.InputStream(device=device, samplerate=SAMPLE_RATE,
                                    channels=1, dtype="float32", callback=cb)
            stream.start()
        except Exception as e:
            self.log(f"микрофон для wake word не открылся: {str(e)[:80]}")
            return
        names = wake_pattern()
        try:
            while not self._wake_stop.wait(WAKE_CHECK):
                if self.should_pause():
                    continue
                with lock:
                    if not frames:
                        continue
                    audio = np.concatenate(frames, axis=0).reshape(-1)
                    frames.clear()
                if audio.size < SAMPLE_RATE * 0.5:
                    continue
                if not get_speech_timestamps(
                        audio, VadOptions(min_speech_duration_ms=200)):
                    continue
                heard = self.transcribe(audio, beam_size=1, vad=False).lower()
                called = names.search(heard)
                if not called:
                    continue
                self.log(f"позвали: {heard!r}")
                # people say the command in the same breath — "кот, включи
                # музыку" — so whatever followed the name is already here and
                # waiting for more speech would just hang
                tail = heard[called.end():].strip(" ,.!?—")
                if self.on_wake:
                    self.on_wake(audio, tail)
        finally:
            stream.stop()
            stream.close()

    def listen_until_quiet(self, max_seconds: float = COMMAND_MAX):
        """Record until the speaker stops. There is no key release to wait for."""
        import numpy as np
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        rec = Recorder()
        try:
            rec.start()
        except Exception as e:
            self.log(f"микрофон занят: {str(e)[:80]}")
            return np.zeros(0, dtype=np.float32)
        start = time.time()
        try:
            while time.time() - start < max_seconds:
                time.sleep(0.3)
                audio = rec.snapshot()
                if audio.size < SAMPLE_RATE * 0.4:
                    continue
                speech = get_speech_timestamps(
                    audio, VadOptions(min_speech_duration_ms=150,
                                      min_silence_duration_ms=COMMAND_SILENCE_MS))
                if not speech:
                    continue
                quiet_ms = (audio.size - speech[-1]["end"]) / SAMPLE_RATE * 1000
                if quiet_ms >= COMMAND_SILENCE_MS:
                    break
        finally:
            audio = rec.stop()
        return audio
