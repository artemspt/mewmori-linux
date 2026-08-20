"""What is open in the browser.

Firefox writes its live session to sessionstore-backups/recovery.jsonlz4 every
few seconds, which is the only place the *whole* tab list exists without an
extension — a window title gives you the one tab in front and nothing else.
The file is mozlz4: an eight-byte magic, the decompressed length, and a raw
LZ4 block. There is no LZ4 in the standard library, so `_lz4` below is the
block decoder, twenty lines and no dependency. Chromium's equivalent is an
undocumented binary journal, so Chromium contributes only its window titles.

Nothing is fetched over the network: a site is described from a small
catalogue of domains, exactly like apps.py does for programs. Reading is
strictly local and read-only.
"""
from __future__ import annotations

import json
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
FIREFOX = HOME / ".mozilla" / "firefox"
MAGIC = b"mozLz40\0"

# what a domain means, in the same spirit as apps.CATALOGUE: enough for the cat
# to have an opinion without anyone fetching the page
SITES = (
    ("github.com", "чужой или свой код на GitHub"),
    ("gitlab", "код в GitLab"),
    ("stackoverflow.com", "хозяин что-то отлаживает и ищет ответ"),
    ("stackexchange.com", "хозяин ищет ответ на технический вопрос"),
    ("huggingface.co", "нейросетевые модели"),
    ("ollama.com", "локальные модели"),
    ("claude.ai", "разговор с другой нейросетью"),
    ("chatgpt.com", "разговор с другой нейросетью"),
    ("openai.com", "чужие нейросети"),
    ("youtube.com", "видео"),
    ("youtu.be", "видео"),
    ("twitch.tv", "стримы"),
    ("habr.com", "статьи для программистов"),
    ("reddit.com", "форумы"),
    ("t.me", "телеграм"),
    ("web.telegram.org", "телеграм в браузере"),
    ("vk.com", "соцсеть"),
    ("docs.python.org", "документация Python"),
    ("developer.mozilla.org", "документация по вебу"),
    ("docs.", "документация"),
    ("wikipedia.org", "энциклопедия"),
    ("localhost", "то, что хозяин запустил у себя"),
    ("127.0.0.1", "то, что хозяин запустил у себя"),
    ("google.com", "поиск"),
    ("yandex.ru", "поиск"),
    ("duckduckgo.com", "поиск"),
    ("aliexpress", "хозяин что-то присматривает"),
    ("ozon.ru", "хозяин что-то присматривает"),
    ("wildberries", "хозяин что-то присматривает"),
    ("steampowered.com", "игры"),
    ("hh.ru", "вакансии"),
)


@dataclass(frozen=True)
class Tab:
    title: str = ""
    url: str = ""
    active: bool = False

    @property
    def domain(self) -> str:
        rest = self.url.split("://", 1)[-1]
        return rest.split("/", 1)[0].split("?", 1)[0].removeprefix("www.").lower()

    @property
    def meaning(self) -> str:
        hay = (self.domain or self.title).lower()
        for key, what in SITES:
            if key in hay:
                return what
        return ""


def _lz4(src: bytes, size: int = 0) -> bytes:
    """LZ4 block format, the decompression half. Enough for mozlz4.

    size is the length the header promises; a mismatch means the file was
    caught mid-write, and half a session file parses as garbage rather than
    failing loudly, so it is checked.
    """
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:                    # literal length is escaped past 15
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n - 1:                   # last sequence is literals only
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        length = token & 15
        if length == 15:
            while True:
                b = src[i]
                i += 1
                length += b
                if b != 255:
                    break
        length += 4                      # minimum match is 4 bytes
        start = len(out) - offset
        if offset >= length:
            out += out[start:start + length]     # the common, non-overlapping case
        else:
            for j in range(length):              # overlapping run — byte at a time
                out.append(out[start + j])
    if size and len(out) != size:
        raise ValueError(f"файл сессии оборван: {len(out)} из {size}")
    return bytes(out)


def _read_mozlz4(path: Path):
    raw = path.read_bytes()
    if raw[:8] != MAGIC:
        raise ValueError("не mozlz4")
    return json.loads(_lz4(raw[12:], struct.unpack("<I", raw[8:12])[0]))


def firefox_tabs() -> list[Tab]:
    """Every open tab in every Firefox window, newest profile first."""
    files = sorted(FIREFOX.glob("*/sessionstore-backups/recovery.jsonlz4"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in files[:1]:               # only the profile actually running
        try:
            data = _read_mozlz4(path)
        except (OSError, ValueError, json.JSONDecodeError, IndexError):
            continue
        for w, win in enumerate(data.get("windows", [])):
            chosen = win.get("selected", 1)
            for t, tab in enumerate(win.get("tabs", []), start=1):
                entries = tab.get("entries") or []
                if not entries:
                    continue
                # `index` is 1-based and points at the current page in this
                # tab's own back/forward history, not at the newest entry
                cur = entries[min(max(tab.get("index", len(entries)), 1),
                                  len(entries)) - 1]
                url = cur.get("url", "")
                if url.startswith(("about:", "moz-extension:")):
                    continue
                out.append(Tab(title=(cur.get("title") or "").strip(), url=url,
                               active=(t == chosen and w == 0)))
    return out


def window_tabs() -> list[Tab]:
    """Front tab of every Chromium-family window, off the window title.

    Chromium keeps its session in a binary journal nobody documents, so this is
    all that can be had cheaply — one title per window, no URL.
    """
    try:
        p = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True,
                           timeout=3, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in p.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        title = parts[3].strip()
        for suffix in (" - Chromium", " - Google Chrome", " - Brave", " — Chromium"):
            if title.endswith(suffix):
                out.append(Tab(title=title[: -len(suffix)].strip(), active=True))
                break
    return out


def open_tabs() -> list[Tab]:
    return firefox_tabs() + window_tabs()


def describe(tabs, limit: int = 12) -> str:
    """The tab list as the model should see it: title, domain, what it means."""
    lines = []
    for t in tabs[:limit]:
        bits = t.title[:70] or t.domain
        if t.domain:
            bits += f"  [{t.domain}]"
        if t.meaning:
            bits += f" — {t.meaning}"
        lines.append(("→ " if t.active else "  ") + bits)
    if len(tabs) > limit:
        lines.append(f"  …и ещё {len(tabs) - limit}")
    return "\n".join(lines)


def domains(tabs) -> set[str]:
    return {t.domain for t in tabs if t.domain}


if __name__ == "__main__":       # python3 -m mewmori.tabs
    found = open_tabs()
    print(f"{len(found)} вкладок")
    print(describe(found, limit=40))
