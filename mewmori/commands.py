"""What the cat can actually do when told: media, windows, programs, text.

Moved over from `speak`'s assist.py. Two things changed on the way.

One: the eight separate "открой браузер" / "открой терминал" / … entries are
gone. Opening something is now a single rule that reads the aliases already in
apps.CATALOGUE, so a new program becomes voice-openable by adding one line
there rather than a command entry, a launcher entry and a tool schema.

Two: the correction flow talks to ollama through chat.py instead of its own
copy of the HTTP plumbing, and the OpenRouter fallback is not carried over —
the cat's promise is that nothing leaves the machine, and a cloud fallback
would quietly break it. Re-adding it means one function, if that promise ever
changes.
"""
from __future__ import annotations

import difflib
import os
import re
import subprocess
import tempfile
import time

from . import apps, chat

PLAYER = "spotify"          # which MPRIS player wins when several are running

CORRECT_PROMPT = (
    "Ты корректор текста. Пользователь мог напечатать текст, забыв переключить "
    "раскладку клавиатуры (например, 'ghbdtn' вместо 'привет'), и/или допустить "
    "опечатки и грамматические ошибки. Верни ТОЛЬКО исправленный текст — без "
    "пояснений, без кавычек, без комментариев. Сохраняй смысл, тон и язык. "
    "Если текст уже корректен, верни его без изменений.\n\nТекст: "
)


def _run(*argv, **kw):
    try:
        return subprocess.run(argv, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, **kw).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def has_active_window() -> bool:
    return _run("xdotool", "getactivewindow")


def active_window() -> tuple[str, str]:
    """(класс, заголовок) окна, которое сейчас перед хозяином.

    "PyCharm is running" and "PyCharm is what you are looking at" are very
    different facts, and the cat was using the first as if it were the second —
    commenting on code at someone who was playing Minecraft.
    """
    try:
        out = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowclassname", "getwindowname"],
            capture_output=True, text=True, timeout=2,
            stdin=subprocess.DEVNULL).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return "", ""
    return (out[0].strip() if out else "",
            out[1].strip() if len(out) > 1 else "")


# -- media -------------------------------------------------------------------
def _player_args() -> list:
    """Browsers register MPRIS players too, and playerctl picks whichever it
    lists first — which is rarely the one the owner meant."""
    try:
        out = subprocess.run(["playerctl", "-l"], capture_output=True,
                             text=True, timeout=3).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return []
    for name in out:
        if PLAYER in name.lower():
            return ["-p", name]
    return []


def media(action: str) -> bool:
    return _run("playerctl", *_player_args(), action)


# -- windows and programs ----------------------------------------------------
_ACTIVATE_JS = """
var list = workspace.windowList();
var best = null, bestArea = -1;
for (var i = 0; i < list.length; i++) {
    var c = list[i];
    var cls = (c.resourceClass || "").toString().toLowerCase();
    if (cls.indexOf("%s") !== -1) {
        var area = c.width * c.height;
        if (area > bestArea) { best = c; bestArea = area; }
    }
}
if (best !== null) { workspace.activeWindow = best; }
"""


def kwin_activate(wm_class: str) -> None:
    """Raise a window through KWin's own scripting API.

    xdotool's windowactivate fails on Plasma 6 here — it errors out reading
    _NET_ACTIVE_WINDOW even though plain `xdotool getactivewindow` works. KWin
    scripting sidesteps that, and picks the biggest matching window, which
    matters for apps like Firefox that register a crowd of tiny helper windows
    under the same class.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(_ACTIVATE_JS % wm_class.lower())
        path = f.name
    plugin = f"mewmori-activate-{os.getpid()}"
    try:
        _run("qdbus6", "org.kde.KWin", "/Scripting",
             "org.kde.kwin.Scripting.loadScript", path, plugin)
        _run("qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start")
        time.sleep(0.15)
        _run("qdbus6", "org.kde.KWin", "/Scripting",
             "org.kde.kwin.Scripting.unloadScript", plugin)
    finally:
        os.unlink(path)


def open_app(app) -> bool:
    """Focus the window if it is already open, otherwise start the program."""
    if not app or not app.launch:
        return False
    if app.wm_class:
        found = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", app.wm_class],
            capture_output=True, text=True)
        if found.stdout.split():
            kwin_activate(app.wm_class)
            return True
    try:
        subprocess.Popen(app.launch, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def show_desktop(want: bool) -> bool:
    """KWin's showDesktop(bool) over qdbus6 silently does nothing, so the
    global shortcut is used instead — but that is a toggle, so it may only be
    fired when the current state is not already the wanted one."""
    current = subprocess.run(
        ["qdbus6", "org.kde.KWin", "/KWin",
         "org.freedesktop.DBus.Properties.Get", "org.kde.KWin", "showingDesktop"],
        capture_output=True, text=True).stdout.strip() == "true"
    if current != want:
        _run("qdbus6", "org.kde.kglobalaccel", "/component/kwin",
             "invokeShortcut", "Show Desktop")
    return True


# -- the phrase table --------------------------------------------------------
# action -> (trigger phrases, what to do, what the cat says it did)
COMMANDS = {
    "media_next": (["следующий трек", "следующая песня", "следующий",
                    "переключи трек", "переключи музыку", "вперёд", "вперед",
                    "скип", "пропусти", "дальше"],
                   lambda: media("next"), "переключил трек"),
    "media_previous": (["предыдущий трек", "предыдущая песня", "предыдущий",
                        "назад", "прошлый трек"],
                       lambda: media("previous"), "вернул прошлый трек"),
    "media_pause": (["пауза", "поставь на паузу", "останови музыку",
                     "стоп музыка", "выключи музыку", "заглуши музыку"],
                    lambda: media("pause"), "заглушил музыку"),
    "media_play": (["включи музыку", "продолжи музыку", "возобнови музыку",
                    "включи плейлист", "запусти плейлист", "играй музыку"],
                   lambda: media("play"), "включил музыку"),
    "show_desktop": (["покажи рабочий стол", "сверни все окна",
                      "скрой все окна", "спрячь все окна"],
                     lambda: show_desktop(True), "убрал окна"),
    "restore_windows": (["верни окна", "разверни окна", "убери рабочий стол",
                         "покажи окна"],
                        lambda: show_desktop(False), "вернул окна"),
    "lock_screen": (["заблокируй экран", "заблокируй компьютер",
                     "закрой сессию"],
                    lambda: _run("loginctl", "lock-session"), "запер экран"),
}

OPEN_VERBS = ("открой", "запусти", "покажи")
_PUNCT = re.compile(r"[,.!?;:\"'«»]")
# collapses conjugations onto the imperative the table is written in, instead
# of listing every verb form as its own alias
_VERBS = {"открою": "открой", "открываю": "открой", "запущу": "запусти",
          "запускаю": "запусти", "включу": "включи", "включаю": "включи",
          "поставлю": "поставь"}
_VERB_RE = re.compile(r"\b(" + "|".join(_VERBS) + r")\b")
FUZZY = 0.75


def normalize(text: str) -> str:
    norm = _PUNCT.sub("", (text or "").lower()).strip()
    return _VERB_RE.sub(lambda m: _VERBS[m.group(1)], norm)


def match(text: str) -> list:
    """Every command present, in the order it was spoken.

    Several fit in one breath — "включи музыку и сверни все окна" — so this
    does not stop at the first hit.
    """
    norm = normalize(text)
    if not norm:
        return []

    # A one-word trigger like "пауза" or "дальше" is only a command when it is
    # most of what was said: inside a sentence it is just a word, and firing on
    # it would pause the music every time the owner uses it in passing.
    words = len(norm.split())
    hits = []
    for action, (phrases, _fn, _said) in COMMANDS.items():
        best = None
        for phrase in phrases:
            if " " not in phrase and words > 4:
                continue
            pos = norm.find(phrase)
            if pos != -1 and (best is None or pos < best):
                best = pos
        if best is not None:
            hits.append((best, action))

    # "открой <что-нибудь из каталога>" is one rule, not one rule per program
    for verb in OPEN_VERBS:
        pos = norm.find(verb)
        if pos == -1:
            continue
        app = apps.by_phrase(norm[pos:])
        if app and app.launch:
            hits.append((pos, f"open:{app.key}"))
            break

    if hits:
        hits.sort()
        seen, ordered = set(), []
        for _pos, action in hits:
            if action not in seen:
                seen.add(action)
                ordered.append(action)
        return ordered

    # nothing matched literally — a misheard proper noun. Fuzzy-match the whole
    # utterance, but only a short one: a long sentence scoring against a short
    # phrase by chance is noise, not intent
    if len(norm.split()) > 6:
        return []
    best_action, best_ratio = None, 0.0
    for action, (phrases, _fn, _said) in COMMANDS.items():
        for phrase in phrases:
            ratio = difflib.SequenceMatcher(None, norm, phrase).ratio()
            if ratio > best_ratio:
                best_action, best_ratio = action, ratio
    return [best_action] if best_ratio >= FUZZY else []


def run(action: str) -> bool:
    if action.startswith("open:"):
        return open_app(apps.BY_KEY.get(action[5:]))
    entry = COMMANDS.get(action)
    return bool(entry[1]()) if entry else False


def describe(action: str) -> str:
    """What the cat should say it just did, in its own words."""
    if action.startswith("open:"):
        app = apps.BY_KEY.get(action[5:])
        return f"открыл {app.name}" if app else "открыл"
    entry = COMMANDS.get(action)
    return entry[2] if entry else action


# -- typing into whatever is focused -----------------------------------------
def type_text(text: str) -> bool:
    """Types into the focused window, or falls back to the clipboard."""
    text = (text or "").strip()
    if not text:
        return False
    if has_active_window() and _run("xdotool", "type", "--clearmodifiers",
                                    "--", text):
        return True
    try:
        subprocess.run(["xclip", "-selection", "clipboard"],
                       input=text.encode("utf8"), check=False)
    except OSError:
        return False
    return True


def backspace(n: int) -> None:
    if n > 0:
        _run("xdotool", "key", "--repeat", str(n), "--delay", "0", "BackSpace")


def grab_field() -> str:
    """Select-all and copy, to see what is in the focused field.

    Deliberately not the X11 PRIMARY selection: PRIMARY is one sticky global
    that keeps whatever was last selected anywhere, with no way to tell a
    fresh highlight from something left over an hour ago.
    """
    _run("xdotool", "key", "ctrl+a")
    time.sleep(0.05)
    _run("xdotool", "key", "ctrl+c")
    time.sleep(0.15)
    try:
        return subprocess.run(["xclip", "-o", "-selection", "clipboard"],
                              capture_output=True, text=True).stdout
    except OSError:
        return ""


def replace_field(text: str) -> None:
    """Re-selects before typing rather than trusting that typing overwrites a
    selection: web and Electron boxes often do not apply that to synthetic
    input, and the selection may be stale after the model round trip."""
    _run("xdotool", "key", "ctrl+a")
    _run("xdotool", "key", "Delete")
    _run("xdotool", "type", "--clearmodifiers", "--", text)


def correct(model: str, on_done) -> None:
    """Fix the focused field: layout, typos, grammar. on_done(text, error)."""
    original = grab_field()
    if not original.strip():
        on_done("", "поле пустое")
        return

    def finished(full, err):
        if err or not full:
            on_done("", err or "модель промолчала")
            return
        fixed = full.strip()
        replace_field(fixed)
        on_done(fixed, "")

    chat.stream(model, [{"role": "user", "content": CORRECT_PROMPT + original}],
                lambda _c: None, finished, options={"temperature": 0.1})
