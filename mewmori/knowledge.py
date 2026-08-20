"""What the cat remembers about its owner, and how long ago it happened.

Three things live here.

**Facts.** One line each, dated, filed under a subject. Stored as plain
markdown with `[[вики-ссылками]]`, so the folder opens in Obsidian as a real
graph you can browse and correct by hand. The cat itself never walks those
links: on every remark it has to answer one question — "which five of these
matter right now" — inside a prompt budget of a few hundred characters. That is
ranking, not traversal, and ranking needs no edges.

**Time in words.** A fact without "three weeks ago" attached is a fact the cat
will keep bringing up forever, long after it stopped being true.

**Weekly compression.** Daily entries pile up faster than any prompt can hold.
Once a week is over it gets squeezed into a few sentences — "много играл в
майнкрафт, кодил Мяумори, друзья звали в дискорд" — and the long memory becomes
those summaries instead of hundreds of raw lines. Nothing is deleted: the
dailies stay on disk, they just stop being what gets read.

No GTK, no packages: this is text files and arithmetic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .memory import BASE, JOURNAL

KNOWLEDGE = BASE / "knowledge"
DIGESTS = JOURNAL / "weekly"

FACT_LINE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2})\s*·\s*(.+?)\s*$")
LINK = re.compile(r"\[\[([^\]]+)\]\]")
WORD = re.compile(r"[\w-]+", re.UNICODE)

# words that match everything and therefore rank nothing
STOP = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще",
    "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли",
    "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь",
    "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей",
    "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя",
    "их", "чем", "была", "сам", "чтоб", "без", "будто", "чего", "раз", "тоже",
    "себе", "под", "будет", "ж", "тогда", "кто", "этот", "того", "потому",
    "этого", "какой", "совсем", "ним", "здесь", "этом", "один", "почти",
    "хозяин", "хозяина", "хозяину", "кот", "кота",
}
MIN_STEM = 4            # letters that must agree before two words count as one
RECENCY_HALF = 21.0     # days at which a fact's weight is halved


@dataclass(frozen=True)
class Fact:
    subject: str
    when: date
    text: str

    @property
    def links(self) -> set:
        return {m.lower() for m in LINK.findall(self.text)}

    def plain(self) -> str:
        """The text without wiki brackets — those are for Obsidian, not models."""
        return LINK.sub(r"\1", self.text)

    def __str__(self):
        return f"{self.when.isoformat()} · {self.text}"


# -- how long ago ------------------------------------------------------------
def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian counts in three shapes, and getting it wrong is audible.

    Public because everything that says "how long" needs it: "возился 1 минут"
    is exactly the kind of wrongness that makes a character sound like a
    template.
    """
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


_plural = plural            # the name the rest of this file was written with


def since(when: date, now: date | None = None) -> str:
    """"вчера", "три недели назад" — what the cat should actually say."""
    now = now or date.today()
    days = (now - when).days
    if days < 0:
        return "только что"
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    if days == 2:
        return "позавчера"
    if days < 7:
        return f"{days} {_plural(days, 'день', 'дня', 'дней')} назад"
    if days < 28:
        weeks = round(days / 7)
        return f"{weeks} {_plural(weeks, 'неделю', 'недели', 'недель')} назад"
    if days < 365:
        months = round(days / 30.4)
        months = max(1, months)
        return f"{months} {_plural(months, 'месяц', 'месяца', 'месяцев')} назад"
    years = round(days / 365.25)
    return f"{years} {_plural(years, 'год', 'года', 'лет')} назад"


# -- storage -----------------------------------------------------------------
def _slug(subject: str) -> str:
    clean = re.sub(r"[^\w\s-]", "", subject, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_]+", "-", clean)[:60] or "разное"


def path_for(subject: str) -> Path:
    return KNOWLEDGE / f"{_slug(subject)}.md"


def add(subject: str, text: str, when: date | None = None) -> bool:
    """One dated line under one subject. Returns False if it was already there."""
    subject, text = subject.strip(), " ".join((text or "").split())
    if not subject or not text:
        return False
    when = when or date.today()
    path = path_for(subject)
    existing = {f.text for f in read(subject)}
    if text in existing:
        return False              # the model repeats itself across days
    try:
        KNOWLEDGE.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"# {subject}\n\n", encoding="utf8")
        with path.open("a", encoding="utf8") as f:
            f.write(f"- {when.isoformat()} · {text}\n")
    except OSError:
        return False
    return True


def read(subject: str) -> list:
    return _parse(path_for(subject), subject)


def _parse(path: Path, subject: str) -> list:
    try:
        lines = path.read_text(encoding="utf8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        m = FACT_LINE.match(line)
        if not m:
            continue
        try:
            out.append(Fact(subject, date.fromisoformat(m.group(1)), m.group(2)))
        except ValueError:
            continue
    return out


def everything() -> list:
    try:
        files = sorted(KNOWLEDGE.glob("*.md"))
    except OSError:
        return []
    out = []
    for path in files:
        subject = path.stem
        try:
            head = path.read_text(encoding="utf8").splitlines()[0]
            if head.startswith("# "):
                subject = head[2:].strip() or subject
        except (OSError, IndexError):
            pass
        out += _parse(path, subject)
    return out


def subjects() -> list:
    return sorted({f.subject for f in everything()})


# -- ranking -----------------------------------------------------------------
def tokens(text: str) -> list:
    return [w for w in (m.group(0).lower() for m in WORD.finditer(text or ""))
            if len(w) > 2 and w not in STOP]


def _agree(a: str, b: str) -> bool:
    """Same word in different cases: "майнкрафт" and "майнкрафте".

    Comparing whole words misses every inflection, and stemming properly needs
    a dictionary. A shared prefix is the cheap middle: crude, but it fails
    towards *not* matching, which is the safe direction for a memory.
    """
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= MIN_STEM and long_.startswith(short[:max(MIN_STEM, len(short) - 2)])


def score(fact: Fact, want: list, now: date) -> float:
    have = tokens(fact.plain()) + tokens(fact.subject) + list(fact.links)
    if not have or not want:
        return 0.0
    hits = sum(1 for w in want if any(_agree(w, h) for h in have))
    if not hits:
        return 0.0
    overlap = hits / len(want)
    days = max(0, (now - fact.when).days)
    recency = RECENCY_HALF / (RECENCY_HALF + days)      # 1.0 today, 0.5 at 21d
    # recency shades the score rather than gating it: something said half a
    # year ago should still surface when it is squarely on topic
    return overlap * (0.55 + 0.45 * recency)


def search(query: str, limit: int = 5, now: date | None = None,
           floor: float = 0.12) -> list:
    now = now or date.today()
    want = tokens(query)
    if not want:
        return []
    ranked = [(score(f, want, now), f) for f in everything()]
    ranked = [(s, f) for s, f in ranked if s >= floor]
    ranked.sort(key=lambda pair: (-pair[0], -pair[1].when.toordinal()))
    return [f for _s, f in ranked[:limit]]


def recall(query: str, limit: int = 5, now: date | None = None) -> str:
    """The lines that go into a prompt, each with how long ago it was."""
    now = now or date.today()
    found = search(query, limit=limit, now=now)
    return "\n".join(f"{since(f.when, now)}: {f.plain()}" for f in found)


def forget(pattern: str) -> int:
    """Drop every fact matching a substring. Returns how many went."""
    needle = (pattern or "").strip().lower()
    if not needle:
        return 0
    gone = 0
    for path in sorted(KNOWLEDGE.glob("*.md")):
        try:
            lines = path.read_text(encoding="utf8").splitlines()
        except OSError:
            continue
        kept = []
        for line in lines:
            m = FACT_LINE.match(line)
            if m and needle in m.group(2).lower():
                gone += 1
                continue
            kept.append(line)
        if gone:
            try:
                if any(FACT_LINE.match(x) for x in kept):
                    path.write_text("\n".join(kept) + "\n", encoding="utf8")
                else:
                    path.unlink()          # nothing left but the heading
            except OSError:
                pass
    return gone


# -- weekly compression ------------------------------------------------------
def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def digest_path(key: str) -> Path:
    return DIGESTS / f"{key}.md"


def undigested(now: date | None = None, keep_days: int = 8) -> list:
    """(key, text) for finished weeks that have not been squeezed yet.

    The current week is never touched, and neither are the last few days: a
    summary written before the week is over would have to be rewritten.
    """
    now = now or date.today()
    weeks = {}
    try:
        files = sorted(JOURNAL.glob("*.md"))
    except OSError:
        return []
    for path in files:
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue                       # weekly/ digests, or something else
        if (now - day).days < keep_days or week_key(day) == week_key(now):
            continue
        try:
            weeks.setdefault(week_key(day), []).append(path.read_text(encoding="utf8"))
        except OSError:
            continue
    return [(key, "\n".join(chunks)) for key, chunks in sorted(weeks.items())
            if not digest_path(key).exists()]


def save_digest(key: str, text: str) -> None:
    try:
        DIGESTS.mkdir(parents=True, exist_ok=True)
        digest_path(key).write_text(f"# {key}\n\n{text.strip()}\n", encoding="utf8")
    except OSError:
        pass


def history(budget: int = 700, weeks: int = 6) -> str:
    """The compressed past, newest first, small enough for a short context."""
    try:
        files = sorted(DIGESTS.glob("*.md"), reverse=True)[:weeks]
    except OSError:
        return ""
    out = []
    for path in files:
        try:
            body = path.read_text(encoding="utf8").split("\n", 1)[-1].strip()
        except OSError:
            continue
        when = _week_started(path.stem)
        label = since(when) if when else path.stem
        out.append(f"{label}: {body}")
    text = "\n".join(out)
    return text[:budget]


def _week_started(key: str):
    try:
        year, week = key.split("-W")
        return date.fromisocalendar(int(year), int(week), 1)
    except (ValueError, AttributeError):
        return None


if __name__ == "__main__":       # python3 -m mewmori.knowledge [запрос]
    import sys

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(f"по запросу {query!r}:\n{recall(query, limit=8) or '  ничего'}")
    else:
        facts = everything()
        print(f"{len(facts)} фактов о {len(subjects())} темах в {KNOWLEDGE}")
        for f in sorted(facts, key=lambda x: x.when, reverse=True)[:15]:
            print(f"  [{f.subject}] {since(f.when)}: {f.plain()[:70]}")
        print(f"\nсжатые недели: {history() or 'нет'}")
