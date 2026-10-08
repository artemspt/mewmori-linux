"""Smallest thing that fails if the rig/clip maths breaks: python3 test_rig.py"""
import hashlib as _hashlib
from pathlib import Path

# Отпечаток живых данных, снятый до всего остального. Сверяется в самом конце:
# проверка, которая пишет в настоящий дневник, колоду или напоминания, ломает
# ровно то, ради чего её писали.
_DATA_DIR = Path.home() / ".local/share/mewmori"


def _fingerprint_data():
    if not _DATA_DIR.exists():
        return {}
    return {str(p.relative_to(_DATA_DIR)): _hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(_DATA_DIR.rglob("*")) if p.is_file()}


_seen_data_before = _fingerprint_data()

from mewmori.rig import Animator, Library, Skin

ASSETS = Path(__file__).parent / "assets"
skin = Skin.load(ASSETS / "skins" / "classic_cat")
lib = Library(ASSETS / "animation")

assert {p.id for p in skin.parts} >= {"body", "head", "tail", "pupil.left"}

# render._matrices walks skin.parts once, so every parent must come before its children
for d in sorted((ASSETS / "skins").iterdir()):
    seen = set()
    for p in Skin.load(d).parts:
        assert p.parent is None or p.parent in seen, (d.name, p.id)
        seen.add(p.id)

walk = lib.clips["walk"]
body_y = next(t for t in walk.tracks if t.part == "body" and t.property == "y")
assert body_y.sample(0.0) == 0.0
assert abs(body_y.sample(0.21) - 1.5) < 1e-9          # keyframe hit exactly
assert 0.0 < body_y.sample(0.105) < 1.5               # eased between keys

spin = next(t for t in lib.clips["jump"].tracks if t.property == "rotation")
assert spin.sample(1.195) == 360                      # per-key "step" easing holds

talk = next(t for t in lib.clips["talk"].tracks if t.frames)
assert talk.frame(0.0, True) == "head.png"
assert talk.frame(0.31, True) == "head_talk.png"      # fps=3.33 -> swaps at 0.3s

a = Animator(lib, "walk")
pose = a.update(1 / 60)
assert "body" in pose and "tail" in pose
assert "alpha" in pose["eye.left"], "blink overlay must be additive on top of walk"

a.set_state("idle")
a.next_break = 0.0
a.update(0.01)
assert a.busy, "idle must fire a break clip once its timer elapses"

# every state the machine can reach must name clips that actually exist
for name, spec in lib.states.items():
    for c in spec.get("base", []) + spec.get("breaks", {}).get("clips", []):
        assert c["clip"] in lib.clips, (name, c["clip"])
    for c in spec.get("overlays", []):
        assert c in lib.clips, (name, c)

# --- geometry: the cat must sit ON a surface, not float above it -----------
from mewmori import render  # noqa: E402

tex = render.load_textures(skin)
# classic_cat's art fills its textures edge to edge...
assert render.ink_box(tex["body.png"]) == (0, 0, 525, 450)
# ...but five of the skins pad theirs, and that padding must not inflate the
# window box or push the cat off the surface it stands on
padded = render.load_textures(Skin.load(ASSETS / "skins" / "britain_cat"))
assert render.ink_box(padded["head.png"]) == (0, 100, 525, 450)
assert render.ink_box(
    render.load_textures(Skin.load(ASSETS / "skins" / "calico_cat"))["tail.png"]
) == (25, 0, 225, 600)

for d in sorted((ASSETS / "skins").iterdir()):
    s = Skin.load(d)
    lo_x, lo_y, hi_x, hi_y = render.rest_bounds(s, render.load_textures(s), 120)
    assert 60 < hi_y - lo_y <= 120, (d.name, hi_y - lo_y)  # fits the asked height
    assert lo_x < 0 < hi_x, d.name                         # straddles the rig origin

# --- streaming: reasoning must never reach the speech bubble ---------------
from mewmori.chat import _strip_think  # noqa: E402


def _feed(chunks):
    inside, out = False, []
    for c in chunks:
        text, inside = _strip_think(c, inside)
        out.append(text)
    return "".join(out)


assert _feed(["Привет мяу!"]) == "Привет мяу!"
assert _feed(["<think>рассуждаю</think>Готово"]) == "Готово"
# the tags arrive split across chunks, which is the whole reason this is stateful
assert _feed(["Привет", "<think>", "надо подумать", "</think>", " мяу!"]) == "Привет мяу!"
assert _feed(["a<think>x", "y</think>b<think>z", "</think>c"]) == "abc"
assert _feed(["<think>never closed"]) == ""  # still streaming its scratchpad

# --- a reply cut off by the token budget must not end mid-word -------------
from mewmori.chat import tidy  # noqa: E402

# what actually happened on screen: the balloon ended on "ничего особ".
# The contract is that a trimmed reply ends on punctuation, never inside a
# word — which sentence it stops at is the model's business, not this test's.
chopped = ("Новости? Ну ладно-ладно, расскажу! Вот только что ты пошёл "
           "и... в общем, ничего особ")
fixed = tidy(chopped, True)
assert not fixed.endswith("особ"), fixed
assert fixed[-1] in ".!?…", fixed
assert chopped.startswith(fixed.rstrip("…")), fixed   # nothing invented
# a reply that finished on its own is never touched, punctuation or not
assert tidy("мур?", False) == "мур?"
assert tidy("мур", False) == "мур"
assert tidy("Ну ладно, расскажу.", True) == "Ну ладно, расскажу."
# nothing finished in time: drop the half-typed word rather than keep it
assert tidy("вскидываю ушки от удивле", True) == "вскидываю ушки от…"
# and a cut so early that trimming would eat the whole reply keeps the words
assert tidy("Да. а дальше я хотел рассказать про то как всё устро", True) \
    == "Да. а дальше я хотел рассказать про то как всё…"
assert tidy("", True) == ""

# --- music: what counts as a song worth reacting to ------------------------
from mewmori.music import _track  # noqa: E402

real = _track({"mpris:trackid": "/t/1", "xesam:title": "Praise God",
               "xesam:artist": ["Kanye West"], "xesam:album": "Donda"})
assert (real.artist, real.title) == ("Kanye West", "Praise God")
assert str(real) == "Kanye West — Praise God"
assert not real.is_ad

# Spotify serves adverts down the very same interface, with an empty artist
ad = _track({"mpris:trackid": "/com/spotify/ad/c95ef63", "xesam:artist": [""],
             "xesam:title": "Wir liefern Dir Deinen Einkauf"})
assert ad.is_ad, "advert must never reach the cat"
assert _track({"mpris:trackid": "/t/2", "xesam:title": "X", "xesam:artist": [""]}).is_ad

# several artists arrive as a list; albumArtist is the fallback
assert _track({"xesam:artist": ["A", "B"]}).artist == "A, B"
assert _track({"xesam:artist": [""], "xesam:albumArtist": ["Solo"]}).artist == "Solo"
assert _track({}).is_ad  # nothing playing is not a song either

# --- roaming: the cat must never be cut off by a screen edge ---------------
from mewmori.app import roam_rect  # noqa: E402

box = (-48.0, -47.0, 42.0, 49.0)          # left, top, right, bottom around the feet
x0, y0, x1, y1 = roam_rect(box, 0, 0, 1920, 1080)
assert (x0, y0) == (48.0, 47.0), (x0, y0)          # room for the left/top overhang
assert (x1, y1) == (1878.0, 1031.0), (x1, y1)      # ...and the right/bottom one
for fx, fy in ((x0, y0), (x1, y1), (x0, y1), (x1, y0)):
    assert fx + box[0] >= 0 and fy + box[1] >= 0           # nothing past top-left
    assert fx + box[2] <= 1920 and fy + box[3] <= 1080     # nothing past bottom-right

# a second monitor offset from the origin must be handled the same way
x0, y0, x1, y1 = roam_rect(box, 1920, 200, 1280, 720)
assert x0 == 1968.0 and y0 == 247.0, (x0, y0)
assert x1 == 3158.0 and y1 == 871.0, (x1, y1)

# --- reading the user's project -------------------------------------------
import tempfile  # noqa: E402

from mewmori import project  # noqa: E402

# the open file is parsed out of JetBrains' own window title, whichever dash
for dash in (" – ", " — ", " - "):
    pr = project.Project(Path("/x/proj"), "PyCharm", f"proj{dash}main.py")
    assert pr.open_file == "main.py", (dash, pr.open_file)
assert project.Project(Path("/x/proj"), "PyCharm", "proj").open_file == ""
assert project.Project(Path("/x/proj"), "PyCharm").name == "proj"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "src").mkdir()
    (root / ".git").mkdir()
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print(1)\n")
    (root / "src" / "util.c").write_text("int main(){}\n")
    (root / "node_modules" / "pkg" / "junk.py").write_text("x=1\n")
    (root / ".git" / "config").write_text("[core]\n")
    (root / "requirements.txt").write_text("flask\n")
    (root / "README.md").write_text("Настоящее описание проекта.\n")

    listing = project.tree(root)
    assert "src/main.py" in listing, listing
    assert not any("node_modules" in x for x in listing), "мусорные каталоги попали в дерево"
    assert not any(".git" in x for x in listing), ".git попал в дерево"

    langs = project.languages(root)
    assert "Python" in langs and "C" in langs, langs
    assert not any("junk" in x for x in listing)

    # prose must win over a lockfile when describing what a project is
    assert next(project._doc_candidates(root)) == "README.md"

    pr = project.Project(root, "PyCharm", f"{root.name} – main.py")
    d = project.digest(pr, budget=600)
    assert len(d) <= 600, len(d)
    assert "main.py" in d and "Python" in d, d

# -- what the cat notices around it -----------------------------------------
from mewmori import health, notify, tabs  # noqa: E402

# a reading is assembled from raw /proc text, the same on this machine and over
# ssh — this is the sample that would come back from a box in trouble
r = health._parse("сервер", "\n".join([
    "MemTotal:        8000000 kB\nMemAvailable:     400000 kB\n"
    "SwapTotal:       2000000 kB\nSwapFree:         400000 kB",
    health.SEP,
    "12.50 8.20 7.10 3/512 9999",
    health.SEP,
    "4",
    health.SEP,
    "/dev/sda1 100000000 97000000 3000000 97% /",
    health.SEP,
    "9",
    health.SEP,
    "Discharging",
]))
assert r.cpus == 4 and abs(r.load1 - 12.5) < 1e-9, r
assert abs(r.mem_free_pct - 5.0) < 0.1, r.mem_free_pct
assert abs(r.swap_used_pct - 80.0) < 0.1, r.swap_used_pct
assert r.battery == 9 and not r.charging, r
found = dict(health.problems(r))
assert {"сервер:disk", "сервер:mem", "сервер:swap", "сервер:load", "сервер:bat"} == set(found), found
assert "сервер" in found["сервер:disk"], found          # the name has to be in the sentence
assert health.problems(health.Reading(name="тут")) == []   # a healthy machine is silent

# the same complaint must not be made twice, but must return once it has cleared
w = health.Watcher(hosts=[])
assert len(w.news(r, 0.0)) == 5
assert w.news(r, 1.0) == []                            # still broken: stay quiet
assert w.news(health.Reading(name="сервер"), 2.0) == []  # recovered
assert len(w.news(r, 3.0)) == 5                        # broke again: say so

# dbus-monitor prints the Notify arguments in order; the id is an int and does
# not count, so app/icon/summary/body are the first four *strings*
note = notify.parse_block([
    '   string "Telegram"', "   uint32 0", '   string ""',
    '   string "Лонер"', '   string "предлагает купить сервер"',
    "   array [", '      string "default"', "   ]",
    "   array [", "      dict entry(", '         string "urgency"',
    "            variant             byte 2", "      )", "   ]", "   int32 -1",
])
assert note.app == "Telegram" and note.summary == "Лонер", note
assert note.body == "предлагает купить сервер" and note.urgent, note
# actions and hints must not be mistaken for the message itself
assert "default" not in str(note), note
assert notify.parse_block(['   string "Мяумори"', "   uint32 0", '   string ""',
                           '   string "мяу"', '   string ""']) is None
assert notify.parse_block(['   string "x"', '   string "y"']) is None   # truncated

# LZ4 blocks: literals, a non-overlapping copy, and the overlapping run that
# needs the byte-at-a-time path
assert tabs._lz4(bytes([0x50]) + b"abcde") == b"abcde"          # literals only
# token 0x51: five literals, then a five-byte match five bytes back
assert tabs._lz4(bytes([0x51]) + b"abcde" + bytes([5, 0])) == b"abcdeabcde"
# token 0x10: one literal, then a four-byte match one byte back — the run that
# only comes out right if the copy is done a byte at a time
assert tabs._lz4(bytes([0x10]) + b"a" + bytes([1, 0])) == b"aaaaa"
try:
    tabs._lz4(bytes([0x50]) + b"abcde", size=99)
except ValueError:
    pass
else:
    raise AssertionError("оборванный файл сессии должен падать, а не отдавать мусор")

t = tabs.Tab(title="Qwen", url="https://www.huggingface.co/models?x=1")
assert t.domain == "huggingface.co" and t.meaning, t
assert tabs.Tab(url="about:blank").domain == "about:blank"
assert tabs.domains([t, tabs.Tab()]) == {"huggingface.co"}
assert "huggingface.co" in tabs.describe([t])

# Claude Code's state file: only two transitions are worth interrupting for,
# and a turn that finished while the owner was watching is not one of them
import json  # noqa: E402
import time  # noqa: E402

from mewmori import claude  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    state = Path(tmp) / "state.json"
    claude.STATE_FILES = (state,)

    def write_state(name):
        state.write_text(json.dumps({"state": name, "ts": time.time()}))

    write_state("writing")
    w = claude.Watcher()
    assert w.poll(0.0) is None                       # no change, nothing to say
    write_state("question")
    kind, text = w.poll(1.0)
    assert kind == "question" and "Клод" in text, text
    write_state("success")
    assert w.poll(5.0) is None, "короткий ход хозяин видел сам"
    write_state("writing")
    w.poll(6.0)
    write_state("success")
    kind, text = w.poll(300.0)
    assert kind == "success" and "минут" in text, text

    # a session that died without its Stop hook must not pin the cat forever
    state.write_text(json.dumps({"state": "writing", "ts": time.time() - claude.STALE - 1}))
    assert claude.read()[0] == claude.IDLE

# -- telegram errands --------------------------------------------------------
# telethon is optional and may not be installed; parsing and matching are pure
# and must be testable without it
from mewmori import telegram  # noqa: E402

who, what = telegram.parse_command("ответь лонеру что у меня нет таких денег")
assert (who, what) == ("лонеру", "у меня нет таких денег"), (who, what)
assert telegram.parse_command("напиши маме, что буду поздно")[0] == "маме"
assert telegram.parse_command("передай Васе что всё готово")[1] == "всё готово"
# an ordinary remark to the cat must never be mistaken for an errand
assert telegram.parse_command("что ты думаешь про этот код") is None
assert telegram.parse_command("ответь мне") is None      # no message to pass on
assert telegram.parse_command("") is None

peers = [telegram.Peer(1, "Лонер", "loner"), telegram.Peer(2, "Лонгрид"),
         telegram.Peer(3, "Мама"), telegram.Peer(4, "рабочий чат", is_user=False)]
# Russian inflects the name — "лонеру" is dative, and the stored contact is not
found = telegram.match("лонеру", peers)
assert found and found[0].name == "Лонер", [str(p) for p in found]
assert telegram.match("@loner", peers)[0].id == 1
assert telegram.match("мама", peers)[0].id == 3
assert telegram.match("никого", peers) == []
assert str(telegram.Peer(1, "Лонер", "loner")) == "Лонер (@loner)"

# -- hearing yes and no ------------------------------------------------------
# The safe default is what matters here: only a clear yes may send a message to
# another human, and everything else has to come back as "no".
from mewmori import voice  # noqa: E402

assert voice.parse("да") is True
assert voice.parse("Да, отправляй!") is True
assert voice.parse("ага") is True and voice.parse("давай") is True
assert voice.parse("нет") is False
assert voice.parse("нет, не надо") is False
assert voice.parse("отмена") is False
# "да нет" is a refusal in Russian, so the negative has to be checked first
assert voice.parse("да нет, не отправляй") is False
# silence, noise and a misheard word must never count as consent
assert voice.parse("") is None
assert voice.parse("   ") is None
assert voice.parse("мур мяу шшш") is None

# -- voice commands ----------------------------------------------------------
# All of this is pure string work and must pass without numpy, sounddevice,
# pynput or whisper installed — the packages are only needed to actually hear.
from mewmori import apps, commands, ears  # noqa: E402

assert commands.match("следующий трек") == ["media_next"]
assert commands.match("поставь на паузу") == ["media_pause"]
# several commands in one breath, in the order they were spoken
assert commands.match("сверни все окна и включи музыку") == ["show_desktop", "media_play"]
assert commands.match("включи музыку и сверни все окна") == ["media_play", "show_desktop"]
# opening is one rule over the app catalogue, not one entry per program
assert commands.match("открой пайчарм") == ["open:pycharm"]
assert commands.match("запусти браузер") == ["open:browser"]
assert commands.match("открой телеграм") == ["open:telegram"]
# conjugations collapse onto the imperative the table is written in
assert commands.normalize("открою браузер") == "открой браузер"
assert commands.match("открою браузер") == ["open:browser"]
# a question is not a command, and must fall through to the cat
assert commands.match("какая сегодня погода") == []
assert commands.match("") == []
# a long sentence must not fuzzy-match a short phrase by accident
assert commands.match("я вчера думал про то как у нас в офисе сломалась пауза") == []
assert commands.describe("open:pycharm") == "открыл PyCharm"
assert commands.describe("media_next") == "переключил трек"

# the longest alias wins, so one program's short name cannot steal another's
assert apps.by_phrase("открой файловый менеджер").key == "files"
assert apps.by_phrase("открой пайчарм").key == "pycharm"
assert apps.by_phrase("открой чтонибудь") is None
# entries that exist only to be launched must never look like running programs
assert all(not a.patterns for a in apps.CATALOGUE if a.key in ("browser", "editor"))

# "кот" is a real Russian substring: matching it loosely would have the cat
# answering to "который", "скотч" and "работа". Built from a literal list here
# rather than from the settings file, so the check does not depend on — or
# write to — whatever this machine happens to have configured.
from mewmori import config  # noqa: E402

_real_get = config.get
config.get = lambda key, default=None: (["мяумори", "мяуми", "кот"]
                                        if key == "wake_words"
                                        else _real_get(key, default))
wake = ears.wake_pattern()
config.get = _real_get
assert wake.search("кот включи музыку")
assert wake.search("мяумори, привет") and wake.search("мяуми открой браузер")
assert wake.search("коту скажи")            # a case ending is still the name
assert not wake.search("который час")
assert not wake.search("передай скотч")
assert not wake.search("это моя работа")

# -- long memory: dated facts, ranking, squeezed weeks -----------------------
from datetime import date  # noqa: E402

from mewmori import knowledge  # noqa: E402

# Russian counts in three shapes and the cat says these out loud, so a wrong
# ending is not a rounding error, it is audible
today = date(2026, 8, 20)
assert knowledge.since(today, today) == "сегодня"
assert knowledge.since(date(2026, 8, 19), today) == "вчера"
assert knowledge.since(date(2026, 8, 18), today) == "позавчера"
assert knowledge.since(date(2026, 8, 17), today) == "3 дня назад"
assert knowledge.since(date(2026, 8, 15), today) == "5 дней назад"
assert knowledge.since(date(2026, 8, 13), today) == "1 неделю назад"
assert knowledge.since(date(2026, 7, 30), today) == "3 недели назад"
assert knowledge.since(date(2026, 6, 20), today) == "2 месяца назад"
assert knowledge.since(date(2025, 8, 20), today) == "1 год назад"
assert knowledge.since(date(2021, 8, 20), today) == "5 лет назад"
assert knowledge.since(date(2026, 8, 21), today) == "только что"   # clock skew

with tempfile.TemporaryDirectory() as tmp:
    knowledge.KNOWLEDGE = Path(tmp) / "knowledge"
    knowledge.DIGESTS = Path(tmp) / "weekly"

    assert knowledge.add("Лонер", "предлагал сервер за 40к, [[хозяин]] отказался",
                         date(2026, 8, 19))
    assert knowledge.add("майнкрафт", "играл через Prism с NeoForge",
                         date(2026, 8, 10))
    assert knowledge.add("Мяумори", "решил выложить проект в открытый доступ",
                         date(2026, 8, 20))
    # the model repeats itself across days; the same line must not pile up
    assert not knowledge.add("Лонер", "предлагал сервер за 40к, [[хозяин]] отказался",
                             date(2026, 8, 20))
    assert len(knowledge.everything()) == 3

    # a fact is found by an inflected form of its own words
    found = knowledge.search("что там с майнкрафтом", now=today)
    assert found and found[0].subject == "майнкрафт", [str(f) for f in found]
    # ...and by the subject, even when the text does not repeat it
    assert knowledge.search("лонер", now=today)[0].subject == "Лонер"
    # words that appear in every single fact rank nothing, so they are dropped
    # before scoring — a query of only those is a query of nothing
    assert knowledge.tokens("хозяин кот") == []
    assert knowledge.search("хозяин", now=today) == []
    # the wiki-links stay in the file, because that is what makes the folder a
    # graph in Obsidian — the cat just reads through them
    assert "[[хозяин]]" in knowledge.path_for("Лонер").read_text(encoding="utf8")
    assert knowledge.read("Лонер")[0].links == {"хозяин"}
    # and nothing at all comes back for something never mentioned
    assert knowledge.search("квантовая механика", now=today) == []
    assert knowledge.search("", now=today) == []

    # what actually reaches the prompt: the fact, with how long ago it was
    line = knowledge.recall("сервер за сколько предлагали", now=today)
    assert "вчера:" in line, line
    assert "[[" not in line, "вики-скобки не должны попадать в промпт"

    # freshness shades the ranking but must not gate it: an older fact that is
    # squarely on topic still has to win over a newer one that is not
    older = knowledge.search("prism neoforge", now=today)
    assert older and older[0].subject == "майнкрафт", [str(f) for f in older]

    # the folder is meant to be opened in Obsidian and edited by hand, so a
    # file of plain prose with no dated bullets is a normal thing to find.
    # Forgetting something in *another* file must not touch it.
    hand = knowledge.KNOWLEDGE / "мои-заметки.md"
    hand.write_text("# мои заметки\n\nсюда я пишу сам, без дат\n", encoding="utf8")

    assert knowledge.forget("сервер за 40к") == 1
    assert len(knowledge.everything()) == 2
    assert hand.exists(), "забывание в одном файле снесло чужой файл целиком"
    assert "сюда я пишу сам" in hand.read_text(encoding="utf8")
    assert knowledge.forget("ничего такого нет") == 0
    hand.unlink()

    # weeks: the current one and the last few days are never squeezed, because
    # a summary written before the week ended would have to be rewritten
    knowledge.JOURNAL = Path(tmp) / "journal"
    knowledge.JOURNAL.mkdir(parents=True)
    for day, text in ((date(2026, 8, 3), "играл в майнкрафт"),
                      (date(2026, 8, 5), "кодил Мяумори"),
                      (date(2026, 8, 19), "вчерашнее"),
                      (today, "сегодняшнее")):
        (knowledge.JOURNAL / f"{day.isoformat()}.md").write_text(text, encoding="utf8")
    due = knowledge.undigested(now=today)
    assert [key for key, _ in due] == ["2026-W32"], due
    assert "майнкрафт" in due[0][1] and "Мяумори" in due[0][1]

    knowledge.save_digest("2026-W32", "Много играл в майнкрафт и кодил Мяумори.")
    assert knowledge.undigested(now=today) == []      # done once, not again
    assert "майнкрафт" in knowledge.history()

# -- notes and reminders ------------------------------------------------------
# Time is parsed by regex and not by the model on purpose: a model that is off
# by an hour is a missed pill, and one that thinks for four seconds is not what
# "через десять минут" means.
from datetime import datetime as _dt, timedelta as _td  # noqa: E402

from mewmori import remind  # noqa: E402

_now = _dt(2026, 8, 21, 9, 30)

got = remind.parse("кот сегодня в 15 часов дня мне нужно выпить таблетку", _now)
assert got and got.text == "выпить таблетку", got
assert got.when().hour == 15 and got.when().minute == 0, got.when()
# the day is today, not tomorrow: 15:00 has not happened yet at 09:30
assert got.when().date() == _now.date()

assert remind.parse("кот напомни через 20 минут проверить сборку", _now).when() \
    == _now + _td(minutes=20)
assert remind.parse("кот поставь таймер на 5 минут", _now).when() == _now + _td(minutes=5)
assert remind.parse("кот напомни через полчаса про чайник", _now).when() \
    == _now + _td(minutes=30)
# "в 8 вечера" is 20:00, not 08:00
assert remind.parse("кот в 8 вечера не забудь позвонить маме", _now).when().hour == 20
# a time already past today rolls to tomorrow rather than firing at once
past = remind.parse("кот напомни в 9 утра про зарядку", _now)
assert past.when().date() == (_now + _td(days=1)).date(), past.when()
assert remind.parse("мяумори напомни завтра в 9 утра про созвон", _now).when().date() \
    == (_now + _td(days=1)).date()

# a time mentioned in passing is not a request to be woken
assert remind.parse("кот в 15 часов созвон прошёл ужасно", _now) is None
assert remind.parse("что там по проекту", _now) is None
assert remind.parse("кот напомни про сборку", _now) is None      # no time at all
assert remind.parse("", _now) is None

assert remind.spell_left(45) == "через 45 секунд"
assert remind.spell_left(300) == "через 5 минут"
assert remind.spell_left(3600) == "через 1 час"
assert remind.spell_left(7200) == "через 2 часа"

# firing: five minutes ahead, then on the dot — and each exactly once, because
# a flag that is not written back means the same pill announced every minute
base = _now.timestamp()
items = [remind.Reminder(text="таблетка", at=base + 200),
         remind.Reminder(text="созвон", at=base + 3000)]
_before_reminders = remind.PATH.read_bytes() if remind.PATH.exists() else None
fired, warn = remind.due(base, items)
# a list handed in belongs to the caller. Saving it over the real file was not
# a side effect but data loss: these two invented notes replaced the owner's
# actual reminders, and the cat then announced a call he had never scheduled.
assert (remind.PATH.read_bytes() if remind.PATH.exists() else None) == _before_reminders, \
    "проверка перезаписала настоящие напоминания"
assert not fired and [w.text for w in warn] == ["таблетка"], (fired, warn)
fired, warn = remind.due(base, items)
assert not fired and not warn, "предупредили дважды"
fired, warn = remind.due(base + 201, items)
assert [f.text for f in fired] == ["таблетка"], fired
fired, warn = remind.due(base + 202, items)
assert not fired, "сработало дважды"
# what the cat should say before the machine goes off
assert [r.text for r in remind.upcoming(base, items=items)] == ["созвон"]
assert remind.upcoming(base, horizon=60, items=items) == []

# -- giving the video memory back ---------------------------------------------
# The processor is the wrong witness: Minecraft runs at 50% CPU quite happily,
# and what actually breaks is shaders having nowhere to live because the model
# is resident. So the question is whether a game is in front — or whether the
# card is full of something that is not ours.
from mewmori import chat as _chat  # noqa: E402
from mewmori.app import Cat as _Cat  # noqa: E402


class _Desk:
    """Enough Cat for the video-memory decision, with no window at all."""
    def __init__(self, front=None, vram=(0, 0), game_vram=0, ours=False):
        self.front = front
        self.vram = vram
        self.game_vram = game_vram
        # whether ollama is holding a model, sampled on a timer rather than
        # asked per frame — it used to be an HTTP call inside this decision
        self.ours = ours

    playing = _Cat.playing
    _is_heavy_game = _Cat._is_heavy_game


_loaded, _chat.loaded = _chat.loaded, lambda: []
game, editor = apps.BY_KEY["prism"], apps.BY_KEY["pycharm"]
assert _Desk(front=game).playing() is True
assert _Desk(front=editor).playing() is False
assert _Desk(front=None).playing() is False
# A game in front is not by itself a reason. Measured on the real desktop:
# plain Minecraft holds ~1 GB and nobody is short of anything, and the cat used
# to sit mute through the whole session for that.
assert _Desk(front=game, vram=(10774, 12282), game_vram=1064)._is_heavy_game() is False
# the same game with shaders wants several gigabytes — that is the case to
# hand the card over for
assert _Desk(front=game, vram=(11800, 12282), game_vram=5200)._is_heavy_game() is True
assert _Desk(front=editor, vram=(400, 12282))._is_heavy_game() is False
# no game, but the card is nearly full and none of it is ours
assert _Desk(front=None, vram=(11000, 12282))._is_heavy_game() is True
# ...the same fullness while the model *is* loaded is our own doing, not a game.
# `ours` is now sampled on the vram timer instead of asked here: with the card
# full — its normal state while the model is resident — this decision ran an
# HTTP call to ollama on every one of sixty frames a second, and the moment
# /api/ps blipped empty mid-load the cat announced it was out of resources and
# woke up again half a second later.
assert _Desk(front=None, vram=(11000, 12282), ours=True)._is_heavy_game() is False
# no card at all: nothing to give up
assert _Desk(front=None, vram=(0, 0))._is_heavy_game() is False

# -- a question in the balloon is modal ---------------------------------------
# While the cat waits for a Telegram code or a cloud password, the balloon *is*
# the input field. Anything that speaks over it either cancels the wait (the
# keystrokes already typed are lost) or paints over the question while the
# keystrokes keep arriving. Both happened. These are the guards, checked on a
# stand-in rather than a real window so they run without a display.
from mewmori.app import Cat  # noqa: E402


class _Waiting:
    """Just enough Cat for the early-return guards to be exercised."""
    prompting = {"q": "код?", "buf": "543", "secret": False, "cb": None}
    said = "код?\n> 543"
    streaming = True

    type_timer = None
    talking = _Cat.talking

    def _stop_typing(self):
        raise AssertionError("нельзя трогать пузырь, пока в нём ждут ответа")


waiting = _Waiting()
# a phrase already being said is never cut off by another one
speaking = _Waiting()
speaking.prompting = None
speaking.said = "я как раз говорил"
speaking.streaming = True          # mid-delivery
speaking.type_timer = None
assert _Cat.talking(speaking) is True
_Cat.say(speaking, "а трек-то сменился")
assert speaking.said == "я как раз говорил", speaking.said
# ...and the typewriter counts as speaking too
speaking.streaming, speaking.type_timer = False, 42
assert _Cat.talking(speaking) is True
# once the phrase is finished, the next one may take the balloon
speaking.type_timer = None
assert _Cat.talking(speaking) is False

Cat.say(waiting, "диск кончается")
assert waiting.said == "код?\n> 543", waiting.said
Cat.type_out(waiting, "и ещё")
assert waiting.said == "код?\n> 543", waiting.said
# a second question would strand the first one's callback forever
Cat.prompt(waiting, "а теперь пароль?", lambda _a: None)
assert waiting.prompting["q"] == "код?", waiting.prompting
# ...and a remark must be dropped rather than cancel the wait
assert Cat.ask(waiting, "заиграла музыка, скажи что-нибудь") is None
assert waiting.prompting is not None and waiting.prompting["buf"] == "543"


# -- the картотека stops shredding itself ---------------------------------
# Thirty-two topics holding fifty-seven facts, most of them the previous
# night's fact reworded: "инструмент" and "инструменты" as separate files,
# "Настраивает PostgreSQL и репликацию" filed next to "Хозяин настраивает
# PostgreSQL и диагностирует сетевые проблемы". recall() then spent all three
# of its slots on one thing said three ways, which is what "кот тупит" was.
import shutil  # noqa: E402

_kb = Path(tempfile.mkdtemp()) / "knowledge"
_kb.mkdir(parents=True)
_real_kb, knowledge.KNOWLEDGE = knowledge.KNOWLEDGE, _kb
try:
    assert knowledge.alike("Настраивает PostgreSQL и репликацию на сервере",
                           "Хозяин настраивает PostgreSQL и репликацию сервера")
    # different things that share a word are not the same thing
    assert not knowledge.alike("Хозяин играет в майнкрафт",
                               "Хозяин пишет мод для майнкрафта на Java")

    assert knowledge.add("инструменты", "Пользуется Claude Code")
    # a synonym of an existing topic lands in the existing file
    assert knowledge.path_for("инструмент") == knowledge.path_for("инструменты")
    # ...and a restatement of a fact already there is not filed again
    assert not knowledge.add("инструмент", "Пользуется Claude Code")
    assert not knowledge.add("инструмент", "Пользуется code Claude")
    assert knowledge.add("инструмент", "Держит Obsidian для заметок")
    assert len(knowledge.everything()) == 2

    # tidy() cleans what piled up before the write path started guarding
    (_kb / "сервер.md").write_text(
        "# Сервер\n\n- 2026-08-20 · Хозяин держит сервер Minecraft\n", encoding="utf8")
    (_kb / "серверы.md").write_text(
        "# Серверы\n\n- 2026-08-21 · Хозяин держит Minecraft сервер\n"
        "- 2026-08-21 · Арендует VPS у стороннего провайдера\n", encoding="utf8")
    merged, dropped = knowledge.tidy()
    assert (merged, dropped) == (1, 1), (merged, dropped)
    assert len(knowledge.subjects()) == 2, knowledge.subjects()
    server = [f for f in knowledge.everything() if "ервер" in f.subject]
    assert len(server) == 2, server
    # the surviving line keeps the earlier date — when it was first said
    assert min(f.when for f in server) == date(2026, 8, 20)

    # and recall never spends two slots on one fact worded twice
    knowledge.KNOWLEDGE = _kb2 = Path(tempfile.mkdtemp()) / "k"
    _kb2.mkdir(parents=True)
    (_kb2 / "a.md").write_text(
        "# A\n\n- 2026-08-20 · Хозяин играет в майнкрафт по вечерам\n", encoding="utf8")
    (_kb2 / "b.md").write_text(
        "# B\n\n- 2026-08-21 · Хозяин по вечерам играет в майнкрафт\n"
        "- 2026-08-21 · Хозяин пишет кота на питоне\n", encoding="utf8")
    found = knowledge.search("майнкрафт питон", limit=3, now=date(2026, 8, 22))
    assert len(found) == 2, [f.text for f in found]
finally:
    knowledge.KNOWLEDGE = _real_kb
    shutil.rmtree(_kb.parent, ignore_errors=True)


# -- listen.py calls cat.ask() by position --------------------------------
# It shipped passing four arguments to a three-argument method, so every voice
# question — the wake word, F1, anything said into the chord that was not a
# command — raised inside a GLib idle callback and the cat simply said nothing.
# A signature check is cheap; catching this by ear cost an evening.
import ast
import inspect

_here = Path(__file__).parent
_src = ast.parse((_here / "mewmori/listen.py").read_text(encoding="utf8"))
_calls = 0
for node in ast.walk(_src):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "idle_add" and node.args):
        continue
    target = node.args[0]
    if not isinstance(target, ast.Attribute):
        continue
    limit = len(inspect.signature(getattr(Cat, target.attr, lambda: 0)).parameters) \
        if hasattr(Cat, target.attr) else None
    if limit is None:
        continue
    _calls += 1
    assert len(node.args) <= limit, (
        f"listen.py передаёт в cat.{target.attr} {len(node.args) - 1} "
        f"аргументов, а он принимает {limit - 1}")
assert _calls >= 3, f"ожидал вызовы cat.* из listen.py, нашёл {_calls}"


# -- подсказка самому себе не остаётся в разговоре -------------------------
# «Пора: «созвон». Скажи ему об этом» — это инструкция коту, а не реплика
# хозяина. Лёжа в истории, она попадала в промпт следующих шести ответов, и
# кот возвращался к одному напоминанию пять раз подряд.
_hist_calls = ast.parse((_here / "mewmori/app.py").read_text(encoding="utf8"))
_ask_fn = next(n for n in ast.walk(_hist_calls)
               if isinstance(n, ast.FunctionDef) and n.name == "ask")
_appends = [n for n in ast.walk(_ask_fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "append"
            and getattr(n.func.value, "attr", "") == "history"]
assert _appends, "ask() должен класть в историю хотя бы реплику хозяина"
for _call in _appends:
    _guards = [a for a in ast.walk(_ask_fn)
               if isinstance(a, ast.If) and _call in list(ast.walk(a))]
    assert any(isinstance(g.test, ast.Name) and g.test.id == "from_owner"
               for g in _guards), "запись в историю обязана быть под from_owner"


# -- «создай карточки с неправильными глаголами» --------------------------
# Просьба на набор не подходила ни под один разбор и уходила в обычную реплику,
# так что кот на неё просто болтал. И разбор ответа модели, которая обещала
# «по одной паре на строку» и всё равно присылает нумерацию и звёздочки.
from mewmori.app import DECK_RE, CARD_RE, parse_pairs  # noqa: E402

for _said, _topic in (("создай карточки с неправильными глаголами", "неправильными глаголами"),
                      ("кот, сделай карточки по фразовым глаголам", "фразовым глаголам"),
                      ("накидай карточек про путешествия", "путешествия"),
                      ("сгенерируй карточки на тему еда", "еда")):
    _m = DECK_RE.match(" ".join(_said.split()))
    assert _m and _m.group("topic") == _topic, (_said, _m and _m.group("topic"))
# одно слово — это другой разбор, и он не должен ловиться набором
assert DECK_RE.match("запомни слово brittle") is None
assert CARD_RE.match("создай карточки с неправильными глаголами") is None
assert DECK_RE.match("как дела") is None

_reply = """Вот список:
1. go, went, gone | идти
2. **take, took, taken** — брать
- be, was, been – быть
run, ran, run | бежать
строка без разделителя
go, went, gone | дубль этой же пары
"""
_pairs = parse_pairs(_reply)
assert _pairs == [("go, went, gone", "идти"), ("take, took, taken", "брать"),
                  ("be, was, been", "быть"), ("run, ran, run", "бежать")], _pairs
assert parse_pairs("совсем не то, что просили") == []


# -- every microphone is closed on the way out ----------------------------
# An open PortAudio stream still live when the interpreter runs sounddevice's
# atexit hook aborts the process inside Pa_Terminate. What that looked like
# from the desk: kill did nothing, and the cat stayed painted on the screen
# with its frame loop already dead. So Listener.stop() has to close every
# Recorder it owns — and a fourth one added later must not be forgotten.
_listen = ast.parse((_here / "mewmori/listen.py").read_text(encoding="utf8"))
_cls = next(n for n in ast.walk(_listen)
            if isinstance(n, ast.ClassDef) and n.name == "Listener")
_init = next(n for n in _cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
_stop = next(n for n in _cls.body if isinstance(n, ast.FunctionDef) and n.name == "stop")
_owned = {t.attr for n in ast.walk(_init) if isinstance(n, ast.Assign)
          for t in n.targets
          if isinstance(t, ast.Attribute) and isinstance(n.value, ast.Call)
          and getattr(n.value.func, "attr", "") == "Recorder"}
assert _owned == {"rec", "chord_rec", "ask_rec"}, _owned
_closed = {n.attr for n in ast.walk(_stop) if isinstance(n, ast.Attribute)}
assert _owned <= _closed, f"stop() не закрывает микрофоны: {_owned - _closed}"


# -- the deck and the clock check themselves ------------------------------
# Both are self-contained and assert their own arithmetic; running them from
# here keeps `python3 test_rig.py` the one command that checks everything.
from mewmori import cards as _cards, fsrs as _fsrs, screentime as _screentime  # noqa: E402

import io  # noqa: E402
import contextlib  # noqa: E402

from mewmori import typed as _typed  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):
    _cards.demo()
    _screentime.demo()
    _fsrs.demo()        # само уравнение: кривая забывания и обновление состояния
    _typed.demo()       # отсев похожего на пароль из буфера набранного


# -- слова с экрана: только настоящий словарный поиск, не субтитры ------------
# Живьём проверено: iq2 на реальном экране вернул ровно «reason | причина,
# повод», а болтливый VLM свалил в кучу субтитры и названия видео. Фильтр
# должен пропускать первое и не пропускать второе.
from mewmori.app import _looks_english as _eng, parse_pairs as _pp  # noqa: E402

assert _eng("reason") and _eng("go, went, gone")
assert not _eng("причина, повод")          # перевод в лице — это перепутанные стороны
assert not _eng("123") and not _eng("")    # без латиницы — не английское слово

_vision = """YES
reason | причина, повод
So they get some cloudy weather, it's maybe 30-ish degrees. | Так что облачно
Slow English Podcast #2 | Eating and Learning Natural English"""
import re as _re  # noqa: E402
_harvest = [(f, b) for f, b in _pp("\n".join(_vision.splitlines()[1:]))
            if _eng(f) and _re.search(r"[а-яА-Я]", b)
            and len(f) <= 40 and len(f.split()) <= 5]
assert _harvest == [("reason", "причина, повод")], _harvest


# -- проверки не трогают настоящие данные ---------------------------------
# Дважды подряд самопроверка писала в живые файлы: демо счётчика времени
# положило хозяину час выдуманного майнкрафта, а проверка напоминаний —
# «таблетку» и «созвон», которые кот потом объявил вслух. Тест, портящий то,
# что проверяет, хуже отсутствующего теста.
_after = _fingerprint_data()
_changed = [k for k in set(_seen_data_before) | set(_after)
            if _seen_data_before.get(k) != _after.get(k)]
assert not _changed, f"проверки записали в настоящие данные: {_changed}"

print("ok")
