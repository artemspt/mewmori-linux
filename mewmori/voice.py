"""Hearing "да" or "нет" out loud.

Used for one thing only: confirming something the cat is about to send to
another human. That makes the requirements small and the failure mode obvious
— anything that is not clearly a yes means no.

Whisper is not a dependency of this project. It is borrowed: if there is a
`speak` checkout next door with its own virtualenv and models, that
interpreter is used as-is, in a subprocess. Nothing is imported into the cat's
process, so a 1.5 GB model never lands in the memory of a thing that draws a
sprite sixty times a second, and a crash in it cannot take the cat down.

Both halves are overridable in the settings for anyone without that checkout:
point `voice_python` at any Python that can `import faster_whisper`, and
`voice_model` at a model directory or a size name like "small".

Recording is `arecord` — no sounddevice, no numpy, no callbacks. The
microphone is opened only for the few seconds after the cat has asked a
question, and the recording is deleted as soon as it has been read.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from . import config

SECONDS = 4.0           # a yes or a no is short; a long silence is a no anyway
RATE = 16000
TRANSCRIBE_TIMEOUT = 90.0
# ponytail: a fresh interpreter loads the model on every answer, so a yes costs
# ~6 s on the GPU instead of ~1 s. Keep a warm worker process if that starts to
# grate — but not before, since it means owning a subprocess lifecycle.

# where a `speak` checkout usually is, so the common case needs no settings
SPEAK_HINTS = (
    Path.home() / "PycharmProjects" / "speak",
    Path.home() / "speak",
    Path.home() / "projects" / "speak",
)
MODEL_ORDER = ("medium", "small", "large-v3", "large-v2", "base")

YES = re.compile(r"\b(да|ага|угу|давай|отправ\w*|конечно|валяй|ок|окей|"
                 r"хорошо|можно|верно|точно|шли|пиши)\b", re.IGNORECASE)
NO = re.compile(r"\b(нет|не|неа|отмена|отмени|стоп|погоди|подожди|не надо|"
                r"не отправляй|отставить)\b", re.IGNORECASE)

# Run in the borrowed interpreter, not here. Kept as one string because it is
# handed to `python -c`: no file to install, nothing to keep in sync.
SCRIPT = r"""
import sys
from faster_whisper import WhisperModel
# the GPU is worth trying and never worth insisting on: a missing CUDA runtime
# must degrade to a slower answer, not to no answer
try:
    model = WhisperModel(sys.argv[1], device="cuda", compute_type="float16")
except Exception:
    model = WhisperModel(sys.argv[1], device="cpu", compute_type="int8")
segments, _ = model.transcribe(sys.argv[2], language="ru", beam_size=1,
                               vad_filter=True)
print(" ".join(s.text for s in segments).strip())
"""


def _speak_dir() -> Path | None:
    for base in SPEAK_HINTS:
        if (base / "venv" / "bin" / "python").exists():
            return base
    return None


def python_path() -> str:
    """The interpreter that has faster_whisper, or "" if none was found."""
    chosen = str(config.get("voice_python") or "").strip()
    if chosen:
        return os.path.expanduser(chosen)
    base = _speak_dir()
    return str(base / "venv" / "bin" / "python") if base else ""


def model_path() -> str:
    """A local model directory if one is lying about, else a size name."""
    chosen = str(config.get("voice_model") or "").strip()
    if chosen:
        return os.path.expanduser(chosen)
    base = _speak_dir()
    if base:
        for name in MODEL_ORDER:
            if (base / "models" / name / "model.bin").exists():
                return str(base / "models" / name)
    return "small"          # faster_whisper fetches it once, then caches


def available() -> str:
    """Empty when the cat can listen; otherwise what is missing."""
    if not shutil.which("arecord"):
        return "нет arecord — поставь alsa-utils"
    py = python_path()
    if not py:
        return "не нашёл питон с faster_whisper — укажи его в настройках"
    if not Path(py).exists():
        return f"нет такого питона: {py}"
    return ""


def parse(text: str):
    """True, False, or None when it was neither — which is treated as no.

    "нет" is checked first on purpose: "нет, не надо" contains no yes, but
    "да нет" does contain both, and in Russian that phrase means no.
    """
    said = (text or "").strip().lower()
    if not said:
        return None
    if NO.search(said):
        return False
    if YES.search(said):
        return True
    return None


def _record(path: str) -> str:
    try:
        p = subprocess.run(
            ["arecord", "-q", "-f", "S16_LE", "-c", "1", "-r", str(RATE),
             "-d", str(int(SECONDS)), path],
            capture_output=True, text=True, timeout=SECONDS + 10,
            stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)[:100]
    if p.returncode != 0:
        return (p.stderr.strip() or "микрофон не отдал звук").splitlines()[-1][:100]
    return ""


def _cuda_env(py: str) -> dict:
    """pip ships the CUDA runtime inside the virtualenv, where ld cannot see it.

    Without this the GPU path fails with "libcublas.so.12 is not found" and
    every transcription falls back to the CPU — which works, but takes several
    times longer for a word the owner is waiting on.
    """
    env = dict(os.environ)
    root = Path(py).resolve().parent.parent
    libs = [str(p) for p in root.glob("lib/python*/site-packages/nvidia/*/lib")
            if p.is_dir()]
    if libs:
        env["LD_LIBRARY_PATH"] = ":".join(libs + [env.get("LD_LIBRARY_PATH", "")])
    return env


def transcribe(wav: str) -> tuple[str, str]:
    """(text, error). Blocking, and slow — never call it on the GTK thread."""
    py = python_path()
    if not py:
        return "", "нет питона с faster_whisper"
    try:
        p = subprocess.run([py, "-c", SCRIPT, model_path(), wav],
                           capture_output=True, text=True, env=_cuda_env(py),
                           timeout=TRANSCRIBE_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "", "распознавание не уложилось во время"
    except OSError as e:
        return "", str(e)[:100]
    if p.returncode != 0:
        return "", (p.stderr.strip() or "распознавание упало").splitlines()[-1][:120]
    return p.stdout.strip(), ""


def ask(on_done):
    """Record, transcribe, and answer on_done(verdict, heard, error).

    verdict is True, False or None; None means nothing usable was heard, and
    the caller must treat that as a no. Runs on its own thread.
    """
    def work():
        fd, wav = tempfile.mkstemp(prefix="mewmori-", suffix=".wav")
        os.close(fd)
        try:
            err = _record(wav)
            if err:
                on_done(None, "", err)
                return
            heard, err = transcribe(wav)
            on_done(parse(heard) if not err else None, heard, err)
        finally:
            # the owner's microphone is not something to leave lying in /tmp
            try:
                os.unlink(wav)
            except OSError:
                pass

    threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":       # python3 -m mewmori.voice — say something
    why = available()
    if why:
        raise SystemExit(why)
    print(f"питон:  {python_path()}\nмодель: {model_path()}")
    print(f"говори ({SECONDS:.0f} с)…")
    done = threading.Event()

    def show(verdict, heard, err):
        print(f"услышал: {heard!r}\nошибка:  {err or 'нет'}\nвердикт: {verdict}")
        done.set()

    ask(show)
    done.wait(TRANSCRIBE_TIMEOUT + 20)
