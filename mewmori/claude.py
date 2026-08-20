"""What Claude Code is doing, so the cat can react to it.

Claude Code fires hooks on its own lifecycle, and the hooks already installed
on this machine write a state word into ~/.config/speak/pet_state.json (the
other pet reads the same file). Rather than install a second set of hooks that
say the same thing, this reads whichever state file was touched most recently
— including our own, so the feature still works if the other pet is removed:

    "Stop": [{"hooks": [{"type": "command",
              "command": "python3 -m mewmori.claude success"}]}]

States come from the hook names: a prompt was submitted (curious), a tool wants
permission or a question is waiting (question), work is happening (writing), the
turn finished (success). Only two of those are worth interrupting a human for.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mewmori"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
OURS = CACHE / "claude_state.json"
STATE_FILES = (OURS, CONFIG / "speak" / "pet_state.json")

IDLE = "idle"
# a hook fires once when a state begins, never as a heartbeat, so a session
# that died without its Stop hook would pin the cat to "он всё ещё думает"
STALE = 900.0
# "question" counts as working: a turn that ends right after asking something
# still ends, and the Stop hook would otherwise be swallowed
WORKING = ("curious", "writing", "thinking", "question")

def spell(seconds: float) -> str:
    """A duration in Russian that agrees with its number.

    "возился 1 минут" is the sound of a template, not of a cat.
    """
    from .knowledge import plural
    if seconds < 90:
        n = max(1, round(seconds))
        return f"{n} {plural(n, 'секунду', 'секунды', 'секунд')}"
    if seconds < 5400:
        n = round(seconds / 60)
        return f"{n} {plural(n, 'минуту', 'минуты', 'минут')}"
    n = round(seconds / 3600)
    return f"{n} {plural(n, 'час', 'часа', 'часов')}"


MEANING = {
    "question": "Клод чего-то ждёт от хозяина — вопрос или разрешение",
    "success": "Клод закончил работу и молчит",
    "curious": "Клоду только что дали задание",
    "writing": "Клод сейчас работает",
}


def write(state: str) -> None:
    """Used by the hook: `python3 -m mewmori.claude <state>`."""
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = OURS.with_suffix(".tmp")
        tmp.write_text(json.dumps({"state": state, "ts": time.time()}), encoding="utf8")
        os.replace(tmp, OURS)          # a half-written file must never be read
    except OSError:
        pass


def read() -> tuple[str, float]:
    """(state, seconds since it was set). Newest file wins."""
    best = None
    for path in STATE_FILES:
        try:
            data = json.loads(path.read_text(encoding="utf8"))
        except (OSError, json.JSONDecodeError):
            continue
        ts = float(data.get("ts", 0))
        if best is None or ts > best[1]:
            best = (str(data.get("state", IDLE)), ts)
    if best is None:
        return IDLE, 0.0
    age = time.time() - best[1]
    return (IDLE if age > STALE else best[0]), age


class Watcher:
    """Reports state changes worth saying out loud, and only those.

    "Claude started thinking" is not news to someone who just pressed enter, so
    the two reported transitions are the ones that happen while attention has
    already wandered off: a question appearing, and a long turn ending.
    """

    def __init__(self, min_work: float = 25.0):
        self.min_work = min_work    # a turn shorter than this finished in plain sight
        self.state, _ = read()
        self.since = time.monotonic()

    def poll(self, now: float):
        """(kind, sentence) or None. kind is 'question' or 'success'."""
        state, _ = read()
        if state == self.state:
            return None
        was, worked = self.state, now - self.since
        self.state, self.since = state, now

        if state == "question":
            return ("question",
                    "Клод остановился и чего-то ждёт от хозяина: вопрос или "
                    "разрешение на действие")
        if state == "success" and was in WORKING and worked >= self.min_work:
            return ("success", f"Клод закончил работу, возился {spell(worked)}")
        return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        write(sys.argv[1])
    else:
        state, age = read()
        print(f"{state} ({age:.0f} с назад) — {MEANING.get(state, 'ничего не делает')}")
