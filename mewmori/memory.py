"""The cat's journal — where the strong model leaves notes for the fast one.

The split is deliberate. The strong model is pinned to the CPU, where even a
short prompt costs seconds, so it never speaks: it reads code, looks at the
screen, and writes down what it found. The fast model analyses nothing at all
— it only talks, using whatever is already written here.

Everything lives under one directory and nothing outside it is ever touched:
the model chooses the topic of an entry, this module chooses the path.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

BASE = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mewmori"
JOURNAL = BASE / "journal"
NOTES = BASE / "projects"


def _slug(text: str, limit: int = 40) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    return (re.sub(r"[\s_-]+", "-", text) or "без-названия")[:limit]


def write(topic: str, body: str) -> Path | None:
    """File one observation under today's date. Returns the file written."""
    body = (body or "").strip()
    if not body:
        return None
    day = JOURNAL / f"{datetime.now():%Y-%m-%d}.md"
    try:
        day.parent.mkdir(parents=True, exist_ok=True)
        with day.open("a", encoding="utf8") as f:
            f.write(f"\n## {datetime.now():%H:%M} — {topic.strip()}\n\n{body}\n")
    except OSError:
        return None
    return day


def recent(budget: int = 900, days: int = 3) -> str:
    """The tail of the journal, small enough to hand to a short context."""
    try:
        files = sorted(JOURNAL.glob("*.md"), reverse=True)[:days]
    except OSError:
        return ""
    chunks = []
    for f in files:
        try:
            chunks.append(f.read_text(encoding="utf8", errors="replace"))
        except OSError:
            continue
    text = "\n".join(chunks).strip()
    if len(text) <= budget:
        return text
    return "…" + text[-budget:]          # the newest entries are at the end


# -- the conversation itself -------------------------------------------------
# A restarted cat that greets you as a stranger is a worse cat. This is the
# short-term half of its memory: the last few turns, verbatim, so a reply can
# still refer to what was said before the reboot. The long-term half is the
# journal above, which the strong model writes and the fast one reads.
SESSION = BASE / "session.json"
SESSION_TURNS = 8       # how much of the conversation survives a restart
SESSION_STALE = 43200.0  # s after which yesterday's chat is not worth resuming


def load_session(now: float) -> list:
    """The tail of the last conversation, if it is still fresh enough."""
    try:
        data = json.loads(SESSION.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict) or now - data.get("at", 0) > SESSION_STALE:
        return []          # picking up a twelve-hour-old sentence is worse than not
    history = data.get("history")
    if not isinstance(history, list):
        return []
    return [m for m in history[-SESSION_TURNS:]
            if isinstance(m, dict) and m.get("role") and m.get("content")]


def save_session(history: list, now: float) -> None:
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        tmp = SESSION.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": now, "history": history[-SESSION_TURNS:]},
                                  ensure_ascii=False), encoding="utf8")
        os.replace(tmp, SESSION)      # a half-written file must never be read back
    except OSError:
        pass


def forget_session() -> None:
    SESSION.unlink(missing_ok=True)


def note_path(project_path) -> Path:
    return NOTES / f"{_slug(str(project_path).strip('/').replace('/', '-'), 80)}.md"


def read_note(project_path) -> str:
    try:
        return note_path(project_path).read_text(encoding="utf8").strip()
    except OSError:
        return ""


def save_note(project_path, text: str) -> None:
    """The strong model's standing understanding of one project."""
    try:
        p = note_path(project_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.strip(), encoding="utf8")
    except OSError:
        pass
