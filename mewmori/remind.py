"""Заметки и напоминания: «кот, сегодня в 15 часов мне нужно выпить таблетку».

Разбор времени — регулярками, а не моделью. Модель на CPU думает секунды, а
человек, сказавший «через десять минут», хочет, чтобы через десять минут это и
случилось; к тому же модель, ошибившаяся на час, — это пропущенная таблетка.

Каждое напоминание срабатывает дважды: заранее (по умолчанию за пять минут) и
ровно в срок. Отдельно есть повод, не связанный со временем: если хозяин
закрывает все программы подряд, компьютер сейчас выключат — и всё, что было
назначено на ближайший час, лучше сказать сейчас, потому что потом будет некому.

Хранится это в том же дневнике, что и остальное: один JSON рядом с картотекой.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .knowledge import plural
from .memory import BASE

PATH = BASE / "reminders.json"
WARN_BEFORE = 300.0        # s: сказать заранее за пять минут
SHUTDOWN_HORIZON = 3600.0  # s: что попадёт в прощальную сводку

# «кот, ...» / «мяумори, ...» — обращение отрезается до разбора
CALL = re.compile(r"^\s*(?:мяумори|мяуми|кот)[,\s]+", re.IGNORECASE)
# слова, которыми человек помечает намерение запомнить
MARKERS = ("напомни", "напомнить", "запомни", "не забудь", "нужно", "надо",
           "поставь таймер", "разбуди")

HOURS = {"полдень": 12, "полудень": 12, "полночь": 0}
IN_UNITS = (
    (("час", "часа", "часов"), 3600),
    (("минут", "минуты", "минуту"), 60),
    (("секунд", "секунды", "секунду"), 1),
)
WORD_NUMBERS = {
    "один": 1, "одну": 1, "полтора": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "полчаса": 30,
}

# «в 15 часов», «в 15:30», «в 9 утра», «в половину третьего» не поддерживаем —
# первые три покрывают всё, что люди реально говорят вслух
AT_TIME = re.compile(
    r"\bв\s+(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*"
    r"(?:час(?:ов|а)?|ч)?\s*(?P<part>утра|дня|вечера|ночи)?\b",
    re.IGNORECASE)
# «через 20 минут», и отдельно «таймер на 5 минут» — «на» само по себе взять
# нельзя, оно есть в «на работе» и «на завтра»
IN_TIME = re.compile(
    r"\b(?:через|(?:таймер|будильник)\s+на)\s+(?P<n>\d+|[а-яё]+)\s*(?P<unit>[а-яё]+)",
    re.IGNORECASE)
TOMORROW = re.compile(r"\bзавтра\b", re.IGNORECASE)
TODAY = re.compile(r"\bсегодня\b", re.IGNORECASE)


@dataclass
class Reminder:
    text: str
    at: float                      # unix time
    warned: bool = False
    done: bool = False
    made: float = field(default_factory=lambda: datetime.now().timestamp())

    def when(self) -> datetime:
        return datetime.fromtimestamp(self.at)

    def left(self, now: float) -> float:
        return self.at - now

    def __str__(self):
        return f"{self.when():%H:%M} — {self.text}"


def _hour24(hour: int, part: str, now: datetime) -> int:
    """«в 3 дня» → 15, «в 9» вечером → скорее всего 21, а не завтрашние 9 утра."""
    part = (part or "").lower()
    if part in ("дня", "вечера") and hour < 12:
        return hour + 12
    if part == "ночи" and hour == 12:
        return 0
    if part in ("утра", "ночи"):
        return hour % 24
    if not part and hour < 12 and now.hour >= hour:
        # без уточнения: если этот час сегодня уже прошёл, имеется в виду вечер
        return hour + 12 if hour + 12 > now.hour else hour
    return hour % 24


def parse_when(text: str, now: datetime | None = None):
    """Момент, о котором говорит фраза, или None. Прошедшее время — на завтра."""
    now = now or datetime.now()
    said = CALL.sub("", text or "")

    m = IN_TIME.search(said)
    if m:
        raw = m.group("n")
        count = int(raw) if raw.isdigit() else WORD_NUMBERS.get(raw.lower(), 0)
        unit = m.group("unit").lower()
        if raw.lower() == "полчаса":
            return now + timedelta(minutes=30)
        for names, secs in IN_UNITS:
            if any(unit.startswith(n[:4]) for n in names) and count:
                return now + timedelta(seconds=count * secs)
        return None

    m = AT_TIME.search(said)
    if m:
        hour = _hour24(int(m.group("h")), m.group("part"), now)
        minute = int(m.group("m") or 0)
        if hour > 23 or minute > 59:
            return None
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if TOMORROW.search(said) or when <= now:
            when += timedelta(days=1)
        return when

    for word, hour in HOURS.items():
        if word in said.lower():
            when = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            return when + timedelta(days=1) if when <= now else when
    return None


def parse(text: str, now: datetime | None = None):
    """(Reminder) из сказанного, или None если это не про напоминание.

    Требуется и время, и намерение: «в 15 часов созвон прошёл ужасно» — это
    рассказ, а не просьба разбудить.
    """
    said = CALL.sub("", text or "").strip()
    if not said:
        return None
    when = parse_when(said, now)
    if when is None:
        return None
    low = said.lower()
    if not any(mark in low for mark in MARKERS):
        return None
    return Reminder(text=_subject(said), at=when.timestamp())


def _subject(said: str) -> str:
    """Оставить то, о чём напоминать, убрав время и служебные слова."""
    text = AT_TIME.sub("", IN_TIME.sub("", said))
    text = TODAY.sub("", text)      # «сегодня» уже сказано временем
    text = TOMORROW.sub("завтра", text)
    for mark in ("напомни мне", "напомни", "не забудь", "запомни", "поставь",
                 "таймер", "будильник", "мне нужно", "мне надо", "нужно", "надо"):
        text = re.sub(rf"\b{mark}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,.—-")
    return text or "таймер"


def spell_left(seconds: float) -> str:
    """«через 5 минут», «через час» — сколько осталось, по-русски."""
    seconds = max(0, seconds)
    if seconds < 60:
        n = max(1, round(seconds))
        return f"через {n} {plural(n, 'секунду', 'секунды', 'секунд')}"
    if seconds < 3600:
        n = round(seconds / 60)
        return f"через {n} {plural(n, 'минуту', 'минуты', 'минут')}"
    n = round(seconds / 3600)
    return f"через {n} {plural(n, 'час', 'часа', 'часов')}"


# -- хранение ----------------------------------------------------------------
def load() -> list:
    try:
        raw = json.loads(PATH.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append(Reminder(**item))
        except TypeError:
            continue
    return out


def save(items) -> None:
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps([vars(r) for r in items], ensure_ascii=False),
                       encoding="utf8")
        tmp.replace(PATH)
    except OSError:
        pass


def add(reminder) -> list:
    items = [r for r in load() if not r.done]
    items.append(reminder)
    items.sort(key=lambda r: r.at)
    save(items)
    return items


def due(now: float, items=None):
    """(сработавшие, предупредить_заранее) — и то и другое помечается на месте.

    Возвращает объекты, а записывает изменения сразу: пропущенное напоминание
    из-за незаписанного флага прозвучало бы второй раз через минуту.

    **Записывается только своё.** Переданный список принадлежит вызвавшему, и
    сохранять его поверх настоящего файла — это не побочный эффект, а потеря
    чужих данных: проверка в test_rig подавала сюда две выдуманные записи,
    «таблетку» и «созвон», они затирали реальные напоминания, и кот потом
    объявлял хозяину созвон, которого тот никогда не назначал.
    """
    mine = items is None
    items = load() if mine else items
    fired, warn = [], []
    for r in items:
        if r.done:
            continue
        if r.at <= now:
            r.done = True
            fired.append(r)
        elif not r.warned and r.at - now <= WARN_BEFORE:
            r.warned = True
            warn.append(r)
    if mine and (fired or warn):
        save(items)
    return fired, warn


def upcoming(now: float, horizon: float = SHUTDOWN_HORIZON, items=None) -> list:
    """Что назначено на ближайшее время — для прощания перед выключением."""
    items = load() if items is None else items
    return [r for r in items if not r.done and 0 < r.at - now <= horizon]


def forget_done(now: float) -> None:
    """Отработавшее старше суток больше не нужно."""
    keep = [r for r in load() if not r.done or now - r.at < 86400]
    save(keep)


if __name__ == "__main__":       # python3 -m mewmori.remind ["фраза"]
    import sys
    if len(sys.argv) > 1:
        said = " ".join(sys.argv[1:])
        r = parse(said)
        print(f"{said!r} → {r or 'не напоминание'}")
    else:
        now = datetime.now().timestamp()
        items = load()
        print(f"{len(items)} напоминаний в {PATH}")
        for r in items:
            mark = "✓" if r.done else " "
            print(f" {mark} {r}  ({spell_left(r.at - now)})")
