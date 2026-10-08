"""Карточки слов: одна сторона — слово, другая — перевод, между ними интервал.

Deliberately not filed in `knowledge/`. A fact there is something the cat knows
about its owner and may mention in passing; a card is something the owner is
trying to learn, and it needs a schedule, a score and a due date that a dated
markdown bullet has nowhere to keep.

**Четыре оценки, а не две.** «Знаю / не знаю» — это не то, как работает
память: между «не помню совсем» и «помню сразу» лежит разница, которая и
решает, когда слово показывать снова.

Считает это [[fsrs]] — модель с двадцатью одним весом, обученным на сотнях
миллионов настоящих повторов. Здесь только колода: хранение, наборы по темам и
то, что видно окну. Само уравнение и почему именно оно — в `fsrs.py`.

No GTK: study.py is the window, this is the deck.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import fsrs
from .memory import BASE

PATH = BASE / "cards.json"

_CYR = re.compile(r"[а-яА-ЯёЁ]")
_LAT = re.compile(r"[a-zA-Z]")


def good_pair(front: str, back: str) -> bool:
    """Настоящая словарная пара, а не то, что модель насобирала с экрана.

    Спрашиваешь у ассистента одно слово — а на экране разбор: производные,
    примеры, целые предложения, «Apple ecosystem», пояснения вместо перевода.
    Всё это лезло в колоду. Правила, вычитанные из живого мусора:

    - лицо английское (латиница, без кириллицы) и это слово или короткая
      фраза, а не предложение — до трёх слов, без точки на конце;
    - оборот — русский перевод: с кириллицей, без латиницы (иначе это «kind of
      tired — Я вроде…» или «экосистема Apple»), и короткий, а не толкование.
    """
    front, back = (front or "").strip(), (back or "").strip()
    if not front or not back:
        return False
    if not _LAT.search(front) or _CYR.search(front):
        return False
    if len(front) > 30 or len(front.split()) > 3 or front[-1] in ".!?":
        return False
    if not _CYR.search(back) or _LAT.search(back):
        return False
    return len(back) <= 40


def _norm(front: str) -> str:
    """Ключ для сравнения: без регистра, пунктуации и скобочных пометок."""
    core = re.sub(r"\([^)]*\)", "", (front or "").lower())
    return re.sub(r"[^\w\s]", "", core).strip()

# Как это называется на кнопках. Порядок — от «совсем нет» к «сразу», и это же
# оценка FSRS от 1 до 4.
AGAIN, HARD, GOOD, EASY = fsrs.AGAIN, fsrs.HARD, fsrs.GOOD, fsrs.EASY
GRADES = ((AGAIN, "Не помню"), (HARD, "Еле вспомнил"),
          (GOOD, "Вспомнил"), (EASY, "Сразу"))


def load() -> list:
    try:
        data = json.loads(PATH.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return []
    return [c for c in data if isinstance(c, dict) and c.get("front")] \
        if isinstance(data, list) else []


def save(cards: list) -> bool:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cards, ensure_ascii=False, indent=1),
                       encoding="utf8")
        tmp.replace(PATH)
        return True
    except OSError:
        return False


def find(cards: list, front: str) -> dict | None:
    # normalised, so "I hope he'll come." and "I hope he'll come" are one card,
    # not two — that punctuation split was half the duplicates
    want = _norm(front)
    return next((c for c in cards if _norm(c["front"]) == want), None)


def blank(front: str, back: str, note: str = "", when: date | None = None) -> dict:
    """Новая карточка. `stability`/`difficulty` появятся с первым ответом:
    начальное состояние FSRS берёт из весов по оценке, а не из воздуха."""
    when = when or date.today()
    return {"front": front, "back": back, "note": note,
            "stability": None, "difficulty": None,
            "reps": 0, "lapses": 0, "interval": 0,
            "due": when.isoformat(), "added": when.isoformat(), "last": "",
            "topic": "", "right": 0, "wrong": 0}


def add(front: str, back: str, note: str = "", topic: str = "") -> bool:
    """Новая карточка. False, если такое слово уже есть."""
    front, back = (front or "").strip(), (back or "").strip()
    if not front:
        return False
    cards = load()
    if find(cards, front):
        return False
    card = blank(front, back, (note or "").strip())
    card["topic"] = (topic or "").strip()
    cards.append(card)
    return save(cards)


def add_many(pairs, topic: str = "") -> tuple[int, int]:
    """Пачка карточек за одну запись. (сколько новых, сколько уже было).

    Одним сохранением, а не по add() на каждую: набор по теме — это два
    десятка слов, и двадцать перезаписей файла ради них это девятнадцать
    лишних.
    """
    cards = load()
    fresh = 0
    known = 0
    for front, back, *rest in pairs:
        front, back = (front or "").strip(), (back or "").strip()
        if not front:
            continue
        if find(cards, front):
            known += 1
            continue
        card = blank(front, back, (rest[0].strip() if rest else ""))
        card["topic"] = topic.strip()
        cards.append(card)
        fresh += 1
    if fresh:
        save(cards)
    return fresh, known


def remove(front: str) -> bool:
    cards = load()
    card = find(cards, front)
    if not card:
        return False
    cards.remove(card)
    return save(cards)


def forget_topic(topic: str) -> int:
    """Выбросить весь набор разом — если модель насочиняла ерунды."""
    want = (topic or "").strip().lower()
    if not want:
        return 0
    cards = load()
    keep = [c for c in cards if (c.get("topic") or "").lower() != want]
    gone = len(cards) - len(keep)
    if gone:
        save(keep)
    return gone


def topics() -> list:
    """Наборы, которые заводились по теме, и сколько в каждом."""
    counts: dict[str, int] = {}
    for card in load():
        name = (card.get("topic") or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


# -- расписание ---------------------------------------------------------------
def elapsed(card: dict, when: date) -> float:
    """Дней с прошлого ответа. FSRS считает по нему, а не по назначенному сроку.

    Это половина смысла модели: слово, вспомненное на две недели позже срока,
    укрепляется сильнее, чем оно же, повторённое вовремя, — но узнать это можно
    только зная, когда на него ответили в прошлый раз.
    """
    try:
        return max(0.0, (when - date.fromisoformat(card.get("last") or "")).days)
    except ValueError:
        return 0.0


def schedule(card: dict, grade: int, when: date | None = None) -> dict:
    """Подвинуть карточку по FSRS и записать новое состояние памяти."""
    when = when or date.today()
    grade = max(AGAIN, min(EASY, int(grade)))
    stability, difficulty, days = fsrs.review(
        card.get("stability"), card.get("difficulty"), elapsed(card, when), grade)

    card["stability"], card["difficulty"] = round(stability, 4), round(difficulty, 4)
    card["interval"] = int(days)
    card["due"] = (when + timedelta(days=int(days))).isoformat()
    card["last"] = card["seen"] = when.isoformat()
    card["reps"] = int(card.get("reps") or 0) + 1
    if grade == AGAIN:
        card["lapses"] = int(card.get("lapses") or 0) + 1
        card["wrong"] = int(card.get("wrong") or 0) + 1
    else:
        card["right"] = int(card.get("right") or 0) + 1
    return card


def answer(card: dict, grade: int, when: date | None = None) -> dict | None:
    """Записать оценку и назначить следующий показ."""
    front = card["front"] if isinstance(card, dict) else card
    cards = load()
    stored = find(cards, front)
    if stored is None:
        return None
    schedule(stored, int(grade), when)
    save(cards)
    return stored


def due(cards: list | None = None, when: date | None = None) -> list:
    """Что пора показать — самое просроченное первым."""
    when = when or date.today()
    ready = [c for c in (load() if cards is None else cards)
             if _due_date(c) <= when]
    ready.sort(key=_due_date)
    return ready


def practice(exclude: str = "", when: date | None = None) -> list:
    """Вся колода по возрастанию срока — чтобы карточки не кончались.

    Пусто на сегодня — не повод останавливать занятие: тянем те, что ближе
    всего к повтору, наперёд. FSRS всё равно пересчитает срок по ответу, так
    что ранний повтор не ломает расписание, а только чуть его сдвигает.
    """
    key = _norm(exclude)
    rest = [c for c in load() if _norm(c["front"]) != key]
    rest.sort(key=_due_date)
    return rest


def tidy() -> int:
    """Выкинуть мусор и дубли из того, что уже накопилось. Сколько ушло.

    good_pair отсеивает предложения, толкования и «Apple ecosystem»; нормализация
    схлопывает одно слово, записанное дважды. Ручные карточки без перевода
    (пустой оборот) не трогаем — их могли завести с пустой стороной нарочно.
    """
    kept, seen, gone = [], set(), 0
    for card in load():
        front, back = card.get("front", ""), card.get("back", "")
        key = _norm(front)
        manual_blank = back == "" and _LAT.search(front) and not _CYR.search(front)
        if key in seen or not (good_pair(front, back) or manual_blank):
            gone += 1
            continue
        seen.add(key)
        kept.append(card)
    if gone:
        save(kept)
    return gone


def _due_date(card: dict) -> date:
    try:
        return date.fromisoformat(card.get("due", ""))
    except ValueError:
        return date.today()


MATURE = 21             # дней: с этого интервала карточку считают выученной


def stats(when: date | None = None) -> tuple:
    """(всего, к повторению сегодня, выучено)."""
    cards = load()
    return (len(cards), len(due(cards, when)),
            sum(1 for c in cards if int(c.get("interval") or 0) >= MATURE))


def strength(card: dict, when: date | None = None) -> float:
    """Насколько слово сейчас в памяти, 0..1 — для точек под карточкой."""
    stability = card.get("stability")
    if not stability:
        return 0.0
    return fsrs.retrievability(float(stability), elapsed(card, when or date.today()))


def spell_next(card: dict) -> str:
    """«через 6 дней» — чтобы кнопка говорила, что она сделает."""
    days = int(card.get("interval") or 0)
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    if days < 30:
        return f"через {days} дн."
    # с десятыми: 78 дней и 101 день оба округляются до «3 мес.», а это
    # разные кнопки, и подпись под ними не должна выглядеть одинаково
    months = days / 30.4
    return f"через {months:.1f} мес." if months < 10 else f"через {round(months)} мес."


def preview(card: dict, when: date | None = None) -> dict:
    """Что сделает каждая кнопка, не трогая карточку. Для подписей под ними."""
    out = {}
    for grade, _label in GRADES:
        copy = dict(card)
        schedule(copy, grade, when)
        out[grade] = spell_next(copy)
    return out


def demo():
    global PATH
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        PATH = Path(tmp) / "cards.json"
        assert add("serendipity", "счастливая случайность")
        assert not add(" Serendipity ", "дубль")     # same word, any case
        assert add("brittle", "хрупкий")
        assert len(due()) == 2                        # a new card is due at once

        day = date(2026, 8, 23)
        # новая карточка берёт состояние из весов, дальше растёт от него
        card = answer({"front": "serendipity"}, GOOD, day)
        assert card["interval"] == 2 and card["due"] == "2026-08-25", card
        assert card["stability"] and card["difficulty"], card
        grew = answer({"front": "serendipity"}, GOOD, date(2026, 8, 25))
        assert grew["interval"] > card["interval"], (card, grew)
        assert grew["stability"] > card["stability"]

        # «Не помню» роняет стабильность и поднимает трудность, но карточка не
        # обнуляется: накопленное состояние — это и есть её история
        before = find(load(), "serendipity")
        lapsed = answer({"front": "serendipity"}, AGAIN, date(2026, 9, 14))
        assert lapsed["stability"] < before["stability"], (before, lapsed)
        assert lapsed["difficulty"] > before["difficulty"]
        assert lapsed["lapses"] == 1 and lapsed["interval"] >= 1

        # Ради чего всё затевалось: тот же ответ, но разное ожидание. Слово,
        # вспомненное на грани забывания, укрепляется сильнее — этого не видят
        # ни коробки Лейтнера, ни SM-2.
        base = dict(blank("x", "x"), stability=10.0, difficulty=5.0,
                    last="2026-08-23")
        soon = schedule(dict(base), GOOD, date(2026, 8, 24))["stability"]
        late = schedule(dict(base), GOOD, date(2026, 9, 12))["stability"]
        assert late > soon * 1.5, (soon, late)

        # Четыре кнопки — четыре разных будущих, по возрастанию
        grown = dict(blank("mature", "зрелое"), stability=30.0, difficulty=5.0,
                     reps=5, last="2026-07-24")
        spans = {g: schedule(dict(grown), g, day)["interval"] for g, _ in GRADES}
        assert len(set(spans.values())) == 4, spans
        assert spans[AGAIN] < spans[HARD] < spans[GOOD] < spans[EASY], spans
        assert len(set(preview(grown, day).values())) == 4, preview(grown, day)
        # и трудность движется в обратную сторону: хуже ответ — труднее слово
        hardness = {g: schedule(dict(grown), g, day)["difficulty"] for g, _ in GRADES}
        assert hardness[AGAIN] > hardness[HARD] > hardness[GOOD] > hardness[EASY]

        # «сколько сейчас в памяти» падает со временем, а не стоит на месте
        assert strength(grown, date(2026, 8, 23)) > strength(grown, date(2026, 12, 1))

        fresh, known = add_many([("run", "бежать"), ("brittle", "дубль"),
                                 ("swim", "плавать")], topic="глаголы")
        assert (fresh, known) == (2, 1), (fresh, known)
        assert dict(topics())["глаголы"] == 2
        assert forget_topic("глаголы") == 2 and forget_topic("глаголы") == 0
        assert remove("brittle") and not remove("brittle")

        # good_pair: a word with a Russian translation passes; a sentence, an
        # English "translation", a brand, a Russian front — all rejected
        assert good_pair("reason", "причина")
        assert good_pair("go, went, gone", "идти")
        assert not good_pair("I hope he'll come.", "надеюсь")     # предложение
        assert not good_pair("kind", "kind of tired — устал")    # латиница в переводе
        assert not good_pair("Apple ecosystem", "экосистема Apple")
        assert not good_pair("жевание", "chewing")               # перепутаны стороны
        assert not good_pair("hope", "очень длинное толкование на десяток слов подряд")

        # tidy() drops the junk and the punctuation-dupes, keeps clean words and
        # a deliberately blank manual card
        PATH2 = Path(tmp) / "d2.json"
        globals()['PATH'] = PATH2
        save([blank("hope", "надежда"), blank("I hope he'll come.", "надеюсь"),
              blank("Apple ecosystem", "экосистема Apple"),
              blank("HOPE", "дубль регистром"), blank("later", "")])
        assert tidy() == 3, [c["front"] for c in load()]
        assert {c["front"] for c in load()} == {"hope", "later"}

        # practice(): вся колода по сроку, чтобы карточки не кончались
        assert [c["front"] for c in practice()] == ["hope", "later"] \
            or [c["front"] for c in practice()] == ["later", "hope"]
        assert all(c["front"] != "hope" for c in practice(exclude="hope"))
    print("ok")


if __name__ == "__main__":
    demo()
