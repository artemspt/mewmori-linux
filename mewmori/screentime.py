"""Сколько времени сегодня ушло в какую программу, и как долго без перерыва.

Counted from the window that is *in front*, not from the processes that are
running: Minecraft minimised for two hours is not two hours of playing, and the
existing `app_since` in app.py — which measures how long a process has lived —
would happily report it as such.

Two numbers, because they answer different questions. The daily total is what
the cat can say out loud ("сегодня в майнкрафте три часа"); the unbroken run is
what decides when to suggest getting up, and it resets the moment the owner
switches to something else, which is itself a break.

No GTK and no packages: a dict, a JSON file per day, and arithmetic.
"""
from __future__ import annotations

import json
from datetime import date

from .memory import BASE

TIME = BASE / "screentime"
FLUSH = 60.0            # s between writes — a crash may lose a minute, not a day
GAP = 90.0              # s away from a program before its run counts as broken


def path_for(day: date | None = None) -> "object":
    return TIME / f"{(day or date.today()).isoformat()}.json"


def spell(seconds: float) -> str:
    """«3 часа 20 минут». Пишется так, как кот это скажет."""
    total = int(max(0, seconds))
    hours, minutes = total // 3600, (total % 3600) // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин" if minutes else "меньше минуты"


class Tracker:
    """Adds up foreground seconds per program, and keeps today's file current."""

    def __init__(self):
        self.day = date.today()
        self.totals = self._load(self.day)
        self.current = ""       # program key in front right now
        self.run = 0.0          # unbroken seconds in it
        self.idle = 0.0         # seconds since it was last in front
        self._unsaved = 0.0

    @staticmethod
    def _load(day: date) -> dict:
        try:
            data = json.loads(path_for(day).read_text(encoding="utf8"))
        except (OSError, ValueError):
            return {}
        return {k: float(v) for k, v in data.items()
                if isinstance(v, (int, float))} if isinstance(data, dict) else {}

    def tick(self, key: str, dt: float) -> None:
        """One poll: `key` is what is in front now ("" for anything else)."""
        today = date.today()
        if today != self.day:               # crossed midnight mid-session
            self.save()
            self.day, self.totals = today, {}
            self.current, self.run = "", 0.0
        if key:
            self.totals[key] = self.totals.get(key, 0.0) + dt
            self._unsaved += dt
        if key and key == self.current:
            self.run += dt
            self.idle = 0.0
        elif key:
            self.current, self.run, self.idle = key, dt, 0.0
        elif self.current:
            # a glance at the browser is not a break; walking away is
            self.idle += dt
            if self.idle >= GAP:
                self.current, self.run, self.idle = "", 0.0, 0.0
        if self._unsaved >= FLUSH:
            self.save()

    def today(self, key: str = "") -> float:
        return self.totals.get(key, 0.0) if key else sum(self.totals.values())

    def top(self, limit: int = 3) -> list:
        """[(key, seconds)] — на что ушёл день, самое долгое первым."""
        return sorted(self.totals.items(), key=lambda kv: -kv[1])[:limit]

    def save(self) -> None:
        self._unsaved = 0.0
        try:
            TIME.mkdir(parents=True, exist_ok=True)
            path_for(self.day).write_text(
                json.dumps({k: round(v) for k, v in self.totals.items()},
                           ensure_ascii=False, indent=1), encoding="utf8")
        except OSError:
            pass


def demo():
    global TIME
    import tempfile
    from pathlib import Path

    # a temp directory, not the real one: tick() flushes on its own, and the
    # first run of this wrote an hour of imaginary Minecraft into today's file
    real, TIME = TIME, Path(tempfile.mkdtemp()) / "screentime"
    t = Tracker()
    t.totals, t._unsaved = {}, 0.0
    for _ in range(60):
        t.tick("prism", 60.0)               # an hour of Minecraft
    assert t.run == 3600.0 and t.today("prism") == 3600.0
    t.tick("browser", 60.0)                 # switching resets the unbroken run
    assert t.run == 60.0 and t.current == "browser"
    for _ in range(3):
        t.tick("", 60.0)                    # away long enough to count as a break
    assert t.current == "" and t.run == 0.0
    assert t.today() == 3600.0 + 60.0
    assert spell(3600 * 3 + 1200) == "3 ч 20 мин"
    assert spell(90) == "1 мин" and spell(5) == "меньше минуты"
    # the flush landed in the sandbox, which is the whole point of redirecting
    # TIME: the first version of this demo wrote an imaginary hour of Minecraft
    # into the real file, and the cat loaded it on the next start
    t.save()
    assert path_for(t.day).exists() and TIME != real
    TIME = real
    print("ok")


if __name__ == "__main__":
    demo()
