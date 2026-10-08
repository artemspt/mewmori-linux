"""Что хозяин печатал последние минут пять — чтобы кот был в курсе, не спрашивая.

Отдельный глобальный слушатель клавиатуры (тот же pynput, что у голоса), с
кольцом «(время, символ)» на пять минут. Backspace съедает последний символ,
пробел и Enter кладутся как есть, спецклавиши игнорируются — получается грубая,
но узнаваемая расшифровка того, что набиралось.

**Только в памяти и только на просмотр.** Буфер никуда не пишется — ни в
дневник, ни в session.json, ни на диск, — и уходит лишь во временную часть
запроса к локальной модели, которая и так живёт на 127.0.0.1. Перезапуск кота
его стирает.

**Пароли.** Ловится всё, а значит и пароли, если их печатать в обычное поле.
Две меры: `active` выключается, пока кот сам ждёт ввод в пузыре (код телеграма,
облачный пароль), и `_redact` выкидывает из выдачи то, что похоже на секрет —
длинный кусок без пробелов с цифрой и символом, или очень длинный токен целиком
(ссылка, ключ, вставленная строка base64). Обычные слова и фразы остаются.
Это не гарантия — глобального «это поле для пароля» в X11 нет, — а разумный
минимум для того, что хозяин включил сам.

pynput необязателен, как и весь голосовой стек: нет его — буфер просто пуст.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque

from . import keys

WINDOW = 300.0          # с: насколько назад помним

# длинный кусок без пробелов, где есть и цифра, и не-буква — так выглядит пароль;
# и просто очень длинный кусок — ссылка, токен, вставленный ключ
_SECRET = re.compile(r"(?=\S{10,})(?=\S*\d)(?=\S*[^\w\s])\S+|\S{24,}")


def _redact(text: str) -> str:
    return _SECRET.sub("…", text)


class Typed:
    """Кольцо нажатий. `active=False` — не записывать (пузырь ждёт секрет)."""

    def __init__(self):
        self.error = ""
        self.active = True
        self._buf: deque = deque()
        self._lock = threading.Lock()
        self._listener = None
        self._kb = keys._keyboard()
        if self._kb is None:
            self.error = keys.available()

    def start(self):
        if self.error:
            return
        self._listener = self._kb.Listener(on_press=self._press)
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None

    def _press(self, key):
        try:
            if not self.active:
                return
            kb, now = self._kb, time.time()
            with self._lock:
                if isinstance(key, kb.Key):
                    if key == kb.Key.space:
                        self._buf.append((now, " "))
                    elif key in (kb.Key.enter,):
                        self._buf.append((now, "\n"))
                    elif key == kb.Key.backspace and self._buf:
                        self._buf.pop()
                    # прочие спецклавиши (Ctrl, стрелки, F1…) — не текст
                else:
                    ch = getattr(key, "char", None)
                    if ch and ch.isprintable():
                        self._buf.append((now, ch))
                self._trim(now)
        except Exception:
            pass                # исключение здесь убило бы слушатель целиком

    def _trim(self, now):
        cutoff = now - WINDOW
        while self._buf and self._buf[0][0] < cutoff:
            self._buf.popleft()

    def recent(self, seconds: float = WINDOW, limit: int = 300) -> str:
        """Последние `seconds` секунд набранного, без похожего на секреты."""
        now = time.time()
        with self._lock:
            self._trim(now)
            chars = [c for t, c in self._buf if t >= now - seconds]
        text = _redact("".join(chars))
        # схлопнуть переводы строк и хвосты пробелов — в промпте это одна строка
        return " ".join(text.split())[-limit:]


def demo():
    assert _redact("привет как дела") == "привет как дела"
    assert _redact("go went gone идти") == "go went gone идти"
    # пароль: длинный, с цифрой и символом — выкидывается, слова рядом целы
    assert _redact("логин hunter2!xQ9zP пароль") == "логин … пароль"
    # очень длинная строка (ключ/ссылка/base64) целиком
    assert "…" in _redact("aGVsbG9oZWxsb2hlbGxvaGVsbG9oZWxsbw")
    # короткое слово с цифрой не трогаем — это не секрет
    assert _redact("covid19") == "covid19"
    print("ok")


if __name__ == "__main__":
    demo()
