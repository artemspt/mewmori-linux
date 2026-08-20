"""Which of the owner's programs are running, and what each one means.

Detection reads argv[0] — the executable — and never the rest of the command
line. Scanning whole command lines looks tempting but matches any shell that
merely mentions a name, so typing "pycharm" in a terminal would 'launch' an
IDE. Patterns are anchored to path segments for the same reason: "/pycharm/"
matches /opt/pycharm/jbr/bin/java without matching ~/PycharmProjects/....
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class App:
    key: str
    name: str
    meaning: str
    patterns: tuple
    # for launching and focusing by voice: the X11 class of its window, and
    # what to run when no such window exists. Programs the cat only ever
    # *notices* leave these empty.
    wm_class: str = ""
    launch: tuple = ()
    said: tuple = ()          # how the owner calls it out loud
    game: bool = False        # while this is in front, the owner is playing


CATALOGUE = (
    App("telegram", "Telegram", "мессенджер: переписка, общение и работа",
        ("/telegram", "telegram-desktop"),
        wm_class="TelegramDesktop",
        launch=(str(Path.home() / "Загрузки/Telegram/Telegram"),),
        said=("телеграм", "telegram", "телегу")),
    App("discord", "Discord", "общение голосом и текстом, обычно вокруг игр",
        ("/discord",), wm_class="discord", launch=("discord",),
        said=("дискорд", "discord")),
    App("pycharm", "PyCharm", "среда разработки, хозяин пишет код на Python",
        ("/pycharm/",), wm_class="jetbrains-pycharm",
        launch=("/opt/pycharm/bin/pycharm.sh",),
        said=("пайчарм", "пичарм", "pycharm", "пайчарму")),
    App("clion", "CLion", "среда разработки, хозяин пишет код на C и C++",
        ("/clion/",), wm_class="jetbrains-clion",
        launch=("/opt/clion/bin/clion.sh",),
        said=("клион", "clion", "клеон")),
    App("hydra", "Hydra", "игровой лаунчер: хозяин собрался играть",
        ("/hydra",), said=("гидру", "hydra"), game=True),
    App("prism", "Minecraft", "майнкрафт через PrismLauncher",
        ("prismlauncher",), wm_class="Minecraft",
        said=("майнкрафт", "minecraft", "призму"), game=True),
    App("spotify", "Spotify", "музыка",
        ("/spotify",), wm_class="spotify", launch=("spotify",),
        said=("спотифай", "spotify")),
    App("steam", "Steam", "игровая платформа: хозяин собрался играть",
        ("/steam", "steamwebhelper"), wm_class="steam", launch=("steam",),
        said=("стим", "steam"), game=True),
    # these are never watched for, only opened — no patterns, so `running()`
    # ignores them entirely
    App("browser", "Firefox", "браузер", (), wm_class="firefox",
        launch=("firefox",), said=("браузер", "фаерфокс", "firefox")),
    App("terminal", "Konsole", "терминал", (), wm_class="konsole",
        launch=("konsole",), said=("терминал", "консоль")),
    App("files", "Dolphin", "файловый менеджер", (), wm_class="dolphin",
        launch=("dolphin",), said=("файлы", "проводник", "файловый менеджер")),
    App("settings", "Параметры системы", "системные настройки", (),
        wm_class="systemsettings", launch=("systemsettings",),
        said=("настройки", "системные настройки", "параметры")),
    App("editor", "Kate", "текстовый редактор", (), wm_class="kate",
        launch=("kate",), said=("редактор", "блокнот", "кейт")),
    App("claude", "Claude", "десктопный клиент Claude", (),
        wm_class="com.anthropic.Claude", launch=("claude-desktop",),
        said=("клод", "клода", "клот", "claude")),
)
BY_KEY = {a.key: a for a in CATALOGUE}
LAUNCHABLE = {a.key: a for a in CATALOGUE if a.launch}


def running() -> set[str]:
    """Keys of the catalogued programs that have a live process.

    Entries with no patterns are launch-only targets and are skipped: `any()`
    over an empty tuple is False, so they can never match.
    """
    found = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv0 = (entry / "cmdline").read_bytes().split(b"\0", 1)[0]
        except OSError:
            continue
        if not argv0:
            continue
        exe = argv0.decode("utf8", "replace").lower()
        for app in CATALOGUE:
            if app.key not in found and any(p in exe for p in app.patterns):
                found.add(app.key)
    return found


def by_phrase(said: str):
    """The app the owner just named out loud, or None.

    Longest alias first, so "открой файловый менеджер" is not decided by the
    shorter "файлы" belonging to some other entry.
    """
    text = (said or "").lower()
    best, best_len = None, 0
    for app in CATALOGUE:
        for alias in app.said:
            if alias in text and len(alias) > best_len:
                best, best_len = app, len(alias)
    return best


def describe(keys) -> str:
    """One line per program, for handing to a model."""
    return "\n".join(f"{BY_KEY[k].name} — {BY_KEY[k].meaning}"
                     for k in sorted(keys) if k in BY_KEY)


def names(keys) -> str:
    return ", ".join(BY_KEY[k].name for k in sorted(keys) if k in BY_KEY)


def by_window(wm_class: str, title: str = ""):
    """The catalogued program a foreground window belongs to, or None.

    Games are the reason this exists: Minecraft's window class depends on the
    launcher, so the title is checked too rather than trusting the class alone.
    """
    cls, name = (wm_class or "").lower(), (title or "").lower()
    if not cls and not name:
        return None
    for app in CATALOGUE:
        if app.wm_class and app.wm_class.lower() in cls:
            return app
        if app.game and any(alias in name for alias in app.said):
            return app
    return None
