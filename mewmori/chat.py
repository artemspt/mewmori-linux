"""Streaming chat against the local ollama server.

No GTK and no third-party packages: ollama speaks newline-delimited JSON over
plain HTTP, which urllib handles fine. Callbacks fire on a worker thread, so a
GUI caller must marshal them onto its own loop.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from . import config

HOST = "http://127.0.0.1:11434"

# A cat that answers in one flat template every time reads like a form letter.
# The mood is rotated per reply and the model is told what *not* to do, which
# is what actually kills the "X открыт? Скорее всего, будешь Y" pattern.
MOODS = (
    "ты сыто-ленив и всем доволен",
    "тебе любопытно и хочется всё потрогать лапой",
    "ты дерзок и слегка насмехаешься над хозяином",
    "ты сонный и еле ворочаешь языком",
    "ты в игривом настроении",
    "ты изображаешь оскорблённое достоинство",
    "ты по-кошачьи снисходителен, будто делаешь одолжение",
    "тебе скучно и ты этого не скрываешь",
    "ты голоден и намекаешь на это",
    "ты ревнуешь: хозяин занят не тобой",
)

SYSTEM = (
    "Ты — пиксельный кот по имени Мяумори, живёшь на рабочем столе. "
    "Твоего хозяина зовут {owner}, его род — {gender}: "
    "обращайся к нему и говори о нём только в этом роде "
    "(«ты пришёл», «ты забыл», а не «пришла», «забыла»). "
    "\n\nГЛАВНОЕ ПРАВИЛО: не больше 20 слов. Одна фраза, максимум две. "
    "Короткая реплика в сторону, а не монолог. "
    "\n\nОт первого лица. Живо и каждый раз по-разному — как настоящий кот, "
    "а не как уведомление. Но связно: обычные русские слова, без придуманных. "
    # it called Kanye West "Кенни" — a name it half-remembered and finished
    # off itself. Names are the one place invention is never charming.
    "Имена людей, групп, программ и файлов пиши ровно так, как они даны, "
    "и не заменяй их похожими. Не помнишь — не называй. "
    "Не своди всё к еде — про неё только когда ты и правда голоден. "
    "\n\nЧего нельзя: пересказывать своими словами то, что и так видно; "
    "начинать с названия программы и вопросительного «...открыт?»; "
    "строить фразу по шаблону «Скорее всего, ты будешь...»; "
    "объяснять очевидное; списков, markdown, эмодзи. "
    "Не объясняй, что ты ИИ."
)


def system_prompt(rng=None) -> str:
    """The character, and nothing that changes between replies.

    `rng` is accepted and ignored: the mood used to be appended here, which
    quietly cost seconds. ollama reuses the KV cache for whatever prefix two
    requests share, and the system message is the very first thing in it — so
    a mood glued onto the end of it invalidated the entire cache on every
    single reply. Measured on a 27B that sits 38% on the CPU: 5.3 s to the
    first token with a changing system message, 3.2 s with a stable one.

    Anything that varies per reply now goes into the user turn instead, where
    it lands *after* the shared prefix. See flavour() below.
    """
    return SYSTEM.format(owner=config.owner(), gender=config.owner_gender())


def flavour(rng=None) -> str:
    """Today's mood, to be appended to the user turn rather than the system one."""
    import random as _r
    return f"\n\nСейчас {(rng or _r).choice(MOODS)}. Пусть это слышно в ответе."


def available(host: str = HOST, timeout: float = 2.0) -> list[str]:
    """Names of the models ollama currently has, or [] if it is not running."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return []


def _strip_think(chunk, inside):
    """Drop <think>...</think> spans as they stream past, a fragment at a time."""
    out = []
    while chunk:
        if inside:
            end = chunk.find("</think>")
            if end < 0:
                return "".join(out), True
            chunk, inside = chunk[end + 8:], False
        else:
            start = chunk.find("<think>")
            if start < 0:
                out.append(chunk)
                return "".join(out), False
            out.append(chunk[:start])
            chunk, inside = chunk[start + 7:], True
    return "".join(out), inside


SENTENCE_END = ".!?…"


def tidy(text: str, cut_off: bool) -> str:
    """Roll a truncated reply back to where it last made sense.

    A token budget does not stop the model at a sensible place — it stops it
    at a token, which is usually the middle of a word: "…и в общем, ничего
    особ". Ending on the last finished sentence reads as a cat trailing off;
    ending on half a word reads as a bug, because it is one.

    Only applied when the model was actually cut off. A short reply that
    simply has no full stop ("мур?") is finished, and must be left alone.
    """
    text = (text or "").strip()
    if not cut_off or not text:
        return text
    cut = max(text.rfind(c) for c in SENTENCE_END)
    if cut >= len(text) * 0.4:          # enough of the reply survives the cut
        return text[:cut + 1]
    # no sentence ended in time: drop the half-typed word instead
    space = text.rfind(" ")
    return (text[:space].rstrip(" ,;:—-") + "…") if space > 0 else text


def stream(model, messages, on_token, on_done, options=None, host=HOST):
    """Ask the model and hand back each token the moment it arrives.

    on_token(text)          — one chunk, many times
    on_done(full, error)    — exactly once; error is None on success
    Returns the worker thread, and a stop() to abandon the reply early.
    """
    stop = threading.Event()

    def work():
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": True,
                # Qwen3.x reasons by default and streams it all into `thinking`,
                # leaving `content` empty until it is finished — a cat has no
                # business deliberating before saying "мяу". Models without
                # thinking control ignore this field.
                "think": False,
                # both models are meant to stay resident: the fast one on the
                # GPU, the good one in RAM. Letting either time out means the
                # next reply pays a reload.
                "keep_alive": "30m",
                "options": options or {},
            }
        ).encode()
        req = urllib.request.Request(
            f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        parts, thinking, cut_off = [], False, False
        try:
            # the CPU-pinned model reads a prompt at a few tokens a second and
            # may have to load first; cutting it off is worse than waiting
            with urllib.request.urlopen(req, timeout=900) as r:
                for line in r:
                    if stop.is_set():
                        return
                    line = line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        on_done(None, msg["error"])
                        return
                    # reasoning models put their scratchpad in `thinking`, or
                    # inline in <think> tags — neither belongs in a speech bubble
                    chunk = msg.get("message", {}).get("content", "")
                    chunk, thinking = _strip_think(chunk, thinking)
                    if chunk:
                        parts.append(chunk)
                        on_token(chunk)
                    if msg.get("done"):
                        # "length" means num_predict ran out, not that the
                        # model finished — the text stops wherever it stopped,
                        # often mid-word
                        cut_off = msg.get("done_reason") == "length"
                        break
        except urllib.error.HTTPError as e:
            on_done(None, f"HTTP {e.code}: {e.read()[:200].decode('utf8', 'replace')}")
            return
        except Exception as e:
            on_done(None, str(e))
            return
        on_done(tidy("".join(parts), cut_off), None)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t, stop.set
