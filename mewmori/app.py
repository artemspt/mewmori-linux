"""Desktop window + behaviour loop. X11: an undecorated always-on-top RGBA window."""
from __future__ import annotations

import base64
import json
import math
import os
import random
import signal
import sys
import time
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
try:
    gi.require_foreign("cairo")
except ImportError:
    raise SystemExit("Нужен мост pycairo<->GTK:  sudo apt install python3-gi-cairo")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import bed as bed_mod  # noqa: E402
from . import claude as claude_mod  # noqa: E402
from . import ears as ears_mod  # noqa: E402
from . import keys  # noqa: E402
from . import apps, chat, commands, config, health, knowledge, memory, music  # noqa: E402
from . import notify, prefs, project, render, sounds, tabs, telegram, voice  # noqa: E402
from .rig import Animator, Library, Skin  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"
COSMETICS_DIR = ASSETS / "cosmetics"
# one model for everything now; the old MEWMORI_MODEL_FAST still overrides it
MODEL = os.environ.get("MEWMORI_MODEL") or os.environ.get("MEWMORI_MODEL_FAST", "")
BUBBLE_H = 170       # room reserved above the cat for the balloon
BUBBLE_LINGER = 6.0  # s the finished reply stays on screen
TRACK_SETTLE = 5.0   # s a song must survive before it is worth a comment
MUSIC_COOLDOWN = 45.0   # s between remarks about music
IDE_POLL = 20.0         # s between checks for an open IDE
CODE_COOLDOWN = 300.0   # s between unprompted remarks about the code
CPU_CEILING = 60.0      # % busy above which the cat keeps its thoughts to itself
APP_COOLDOWN = 90.0     # s between remarks about programs starting
IDLE_ASK = 300.0        # s without input before the cat checks you are still there
ASK_WAIT = 90.0         # s it waits for an answer before deciding you left
BACK_AT = 8.0           # s of idle below which you count as present again
BED_GRACE = 25.0        # s to wait for the panel applet before giving up on it
SCREEN_MIN = 300.0      # s the screen is never looked at more often than
SCREEN_IDLE = (420.0, 1080.0)   # s of nothing happening before a glance anyway
SCREEN_WIDTH = 1024     # px the screenshot is shrunk to before it is looked at
HEALTH_POLL = 60.0      # s between looks at this machine's disk and memory
REMOTE_POLL = 300.0     # s between ssh round trips to the other machines
TABS_POLL = 45.0        # s between reads of the browser session file
TABS_COOLDOWN = 240.0   # s between remarks about what is open in the browser
CLAUDE_POLL = 2.0       # s between reads of Claude Code's state file
NOTE_HOLD = 600.0       # s a notification waits for a pause before it is forced
NOTE_GAP = 60.0         # s between deliveries of held notifications
FLOW_IDLE = 20.0        # s of not typing that counts as a natural pause
# every source below can fire at once — a build finishing, a song, a tab, a
# notification — so one floor applies to all of them together. Without it the
# cat stops being a cat and becomes a notification daemon with ears.
SPONTANEOUS_GAP = 75.0
TYPE_CPS = 32           # characters a second the cat appears to type
PROMPT_TIMEOUT = 180.0  # s before an unanswered question lets the keyboard go
# While a game is in front, the cat watches the screen half again as often —
# that is the moment there is most to see and least to read.
PLAY_GLANCE = 1.5
# ...and a game keeps the processor busy by definition, so the ordinary
# "the machine is working, keep quiet" ceiling would silence it for the whole
# session. A build still shuts the cat up; a game does not.
PLAY_CEILING = 92.0
FRONT_POLL = 4.0        # s between checks of which window is in front
# The vanishing act. The hide timer is 700 ms, not the clip's full 800: a
# non-loop base clip is restarted by the animator the moment it ends, and the
# restart blends back toward visible — hiding at 700 ms catches the cat while
# it is still fully faded out. Same reasoning puts idle at 600 ms, exactly at
# the end of the appear clip, before it would start fading out again.
VANISH_HIDE_MS = 700    # ms of fade-out before the window is unmapped
VANISH_SHOW_MS = 600    # ms of fade-in before idle takes over
VANISH_AWAY = (15, 15)  # s spent away before coming back in a new skin — 15 sec per request


def _cpu_sample():
    """(busy, own, total) jiffies — everything, the cat's own thinking, and all.

    `own` is what ollama is burning on the cat's behalf. It has to come out of
    the reading, otherwise the strong model pinned to the CPU would hold the
    machine permanently "busy" and the cat would silence itself forever.
    """
    with open("/proc/stat") as f:
        parts = [int(v) for v in f.readline().split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)   # idle + iowait
    total = sum(parts)

    own = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes()[:200].lower()
            if b"ollama" not in cmd and b"llama-server" not in cmd:
                continue
            fields = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
            own += int(fields[11]) + int(fields[12])        # utime + stime
        except (OSError, IndexError, ValueError):
            continue
    return total - idle, own, total
FRAME_MS = 16
WALK_SPEED = 45.0    # px/s at height 120
RUN_SPEED = 190.0
FLATTEN = 0.6        # vertical steps are shorter than horizontal — it is a side view


def roam_rect(bounds, wx, wy, ww, wh):
    """Where the cat's feet may go so that no part of it is ever cut off screen.

    bounds is the cat's drawn box around its feet: (left, top, right, bottom),
    with left/top negative.
    """
    return (wx - bounds[0], wy - bounds[1],
            wx + ww - bounds[2], wy + wh - bounds[3])


class Cat(Gtk.Window):
    def __init__(self, skin_id="", height=0):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.rng = random.Random()
        # the window's whole geometry is derived from the height, so these two
        # are read once here and only change on the next start
        self.height_px = height or int(config.get("height"))
        skin_id = skin_id or config.get("skin")
        self.lib = Library(ASSETS / "animation")
        self.load_skin(skin_id)
        self.anim = Animator(self.lib, "idle", self.rng)

        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        self.work = mon.get_workarea()

        render.ensure_font()
        pad_x = int(max(0.7 * height, render.BUBBLE_W / 2 + 24))
        pad_top = int(max(1.2 * height, BUBBLE_H + 24))
        pad_bot = int(0.4 * height)
        self.win_w = int(self.bounds[2] - self.bounds[0]) + 2 * pad_x
        self.win_h = int(self.bounds[3] - self.bounds[1]) + pad_top + pad_bot
        self.origin = (pad_x - self.bounds[0], pad_top - self.bounds[1])
        self.foot = self.bounds[3]  # px below rig origin where the feet are

        # the cat roams a plain rectangle: the whole work area, inset just enough
        # that no part of it is ever clipped off screen
        wa = self.work
        self.roam = roam_rect(self.bounds, wa.x, wa.y, wa.width, wa.height)
        self.x = self.rng.uniform(self.roam[0], self.roam[2])
        self.y = self.rng.uniform(self.roam[1] + (self.roam[3] - self.roam[1]) * 0.5,
                                  self.roam[3])
        self.in_bed = False
        self.facing = 1      # 1 = the drawn direction, left
        self.target = None
        self.speed = 0.0
        self.plan_in = 1.5
        self.gaze = (0.0, 0.0)
        self.pose = {}
        self.cooldowns = {}
        self.dragging = None
        self.grab_from = (0.0, 0.0)
        self.said = ""          # what is currently in the balloon
        self.streaming = False
        self.bubble_until = 0.0
        self.abort = None
        self.turn = 0       # replies are numbered so stale tokens can be dropped
        # a restarted cat that greets you as a stranger is a worse cat
        self.history = (memory.load_session(time.time())
                        if config.get("remember_session") else [])
        self.model = self._pick_model()
        # the 27B carries the vision projector, so glances cost no extra weights
        self.model_vision = next(
            (n for n in chat.available() if "iq2" in n.lower()), "")
        self.watch_screen = bool(self.model_vision) and bool(config.get("watch_screen"))
        self.idle_poll = 0.0
        self.idle_s = 0.0
        self.asked_at = 0.0         # when "are you there?" was asked
        self.away = False
        self.harvesting = False     # the day is being turned into facts
        self.digesting = None       # which week is being squeezed, if any
        self._idle_proxy = None
        self.apps = set()           # catalogued programs seen running
        self.apps_known = False     # first scan is inventory, not news
        self.app_since = {}         # key -> when it was first seen running
        self.quiet_until = 0.0      # one floor under every unprompted remark
        self.health = health.Watcher()
        self.health_in = 20.0
        self.remote_in = 45.0
        self.tabs = []
        self.domains_seen = set()
        self.tabs_known = False
        self.tabs_in = 5.0
        self.claude = claude_mod.Watcher()
        self.claude_in = CLAUDE_POLL
        self.notes = notify.Listener(start=bool(config.get("watch_notifications")))
        self.break_now = False      # something just ended: a fair moment to interrupt
        self.gap = float(config.get("chatter_gap"))
        self.front = None           # the catalogued program in front, if any
        self.front_in = 0.0
        self.tg = None              # Telegram client, built on the first errand
        self.listening = False      # the microphone is open, waiting for да/нет
        self.type_timer = None      # the typewriter effect in the balloon
        self.prompt_timer = None
        self.prompting = None       # {"q", "buf", "secret", "cb"} while asking
        self.ears = None            # dictation and voice commands, if wanted
        if config.get("voice_enabled"):
            GLib.idle_add(self._start_voice)     # loads a model: not on the way in
        self.screen_at = 0.0        # when the screen was last looked at
        self.screen_due = 120.0     # ...and when it is next worth a look
        self.track = None
        self.track_timer = None
        self.project = None
        self.ide_scan_in = 0.0
        self.cpu_busy = 0.0         # % over the last second, everything but the cat
        self._cpu_prev = _cpu_sample()
        self.cpu_scan_in = 1.0
        try:
            self.music = music.Spotify(self._on_track)
        except Exception:
            self.music = None   # no session bus, no music — the cat lives on
        self.last_ptr = (0, 0)
        self.last_t = time.monotonic()

        # -- the vanishing act --------------------------------------------
        # once in a while the cat slips away for a couple of minutes and
        # comes back wearing a different skin. One flag says a trick is in
        # flight; every timer is kept by id so bed/destroy can cancel them.
        self._vanish_pending = False      # vanish/hidden/appear in progress
        self._vanish_next_timer = None    # GLib source: next booked trick
        self._vanish_hide_timer = None    # GLib source: fade-out -> unmap
        self._vanish_return_timer = None  # GLib source: away -> come back
        self._vanish_appear_timer = None  # GLib source: fade-in -> idle
        self._next_vanish_ts = 0.0        # when the booked trigger fires
        self._vanish_phase = None         # None | "to_corner" | "vanishing" | "hidden"
        self._vanish_origin = None        # (x, y) where the trick started — walk back on return
        self._cosmetic = None             # dict from cosmetic.json or None
        self._cosmetic_surf = None        # cairo surface for the cosmetic texture
        self._cosmetic_slot = None
        self._cosmetic_timer = None       # GLib source for auto-strip in 5 min

        self.bed = bed_mod.PanelBed(self.wake_up, self.go_to_bed)

        self._setup_window()
        GLib.timeout_add(FRAME_MS, self._tick)
        if config.get("vanish_enabled"):
            self._schedule_next_vanish()

    # -- setup ---------------------------------------------------------
    def load_skin(self, skin_id):
        self.skin = Skin.load(ASSETS / "skins" / skin_id)
        self.textures = render.load_textures(self.skin)
        self.bounds = render.rest_bounds(self.skin, self.textures, self.height_px)

    def _setup_window(self):
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_resizable(False)
        self.stick()
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_default_size(self.win_w, self.win_h)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.connect("key-press-event", self._on_key)
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("realize", lambda *_: self._shape())
        self.connect("destroy", Gtk.main_quit)

    def _shape(self):
        """Only the cat's own box catches clicks; the rest of the window is see-through."""
        ox, oy = self.origin
        r = cairo.RectangleInt(
            int(ox + self.bounds[0]) - 4,
            int(oy + self.bounds[1]) - 4,
            int(self.bounds[2] - self.bounds[0]) + 8,
            int(self.bounds[3] - self.bounds[1]) + 8,
        )
        self.input_shape_combine_region(cairo.Region(r))

    # -- behaviour -----------------------------------------------------
    def _plan(self):
        # gone or going: no idle/walk planning until the trick is over
        if self._vanish_pending:
            return
        # Only when the IDE is the window in front. It used to be enough that
        # the IDE was *running*, which meant the cat discussed code at someone
        # halfway through a Minecraft session.
        at_the_ide = bool(self.front and self.front.key in ("pycharm", "clion"))
        if (self.project and at_the_ide and not self.in_bed
                and self.rng.random() < 0.22 and self._cool("code", CODE_COOLDOWN)):
            self._remark_on_code()
            return

        roll = self.rng.random()
        if roll < 0.28:
            # idle is never dead time: the animator fires grooming, yawns and stretches
            self.anim.set_state("idle")
            self.target, self.speed = None, 0.0
            self.plan_in = self.rng.uniform(3, 8)
        elif roll < 0.74:
            self._go(WALK_SPEED, "walk", (90, 520))
        elif roll < 0.90:
            self._go(RUN_SPEED, "run", (300, 900))
        else:
            self.anim.set_state("sleep")
            self.target, self.speed = None, 0.0
            self.plan_in = self.rng.uniform(12, 25)

    def _go(self, speed, state, dist):
        """Head for a random spot inside the roaming rectangle."""
        x0, y0, x1, y1 = self.roam
        for _ in range(8):
            ang = self.rng.uniform(0, 2 * math.pi)
            d = self.rng.uniform(*dist)
            tx = self.x + math.cos(ang) * d
            ty = self.y + math.sin(ang) * d * FLATTEN
            if x0 <= tx <= x1 and y0 <= ty <= y1:
                break
        else:                                  # cornered: just pick anywhere
            tx, ty = self.rng.uniform(x0, x1), self.rng.uniform(y0, y1)
        self.target = (tx, ty)
        self.speed = speed * self.height_px / 120.0
        self.anim.set_state(state)
        self.plan_in = math.hypot(tx - self.x, ty - self.y) / self.speed + 0.4

    def _cursor(self):
        seat = Gdk.Display.get_default().get_default_seat()
        _, x, y = seat.get_pointer().get_position()
        return x, y

    def _cool(self, name, secs):
        now = time.monotonic()
        if now < self.cooldowns.get(name, 0):
            return False
        self.cooldowns[name] = now + secs
        return True

    def _tick(self):
        now = time.monotonic()
        dt = min(now - self.last_t, 0.1)
        self.last_t = now
        if self.in_bed:
            return True          # the bed draws the sleeping cat itself

        self.cpu_scan_in -= dt
        if self.cpu_scan_in <= 0:
            self.cpu_scan_in = 1.0
            busy, own, total = _cpu_sample()
            d_busy = busy - self._cpu_prev[0]
            d_own = own - self._cpu_prev[1]
            d_total = total - self._cpu_prev[2]
            if d_total > 0:
                self.cpu_busy = 100.0 * max(0, d_busy - d_own) / d_total
            self._cpu_prev = (busy, own, total)

        self.idle_poll -= dt
        if self.idle_poll <= 0:
            self.idle_poll = 3.0
            self._check_presence(now)

        self.ide_scan_in -= dt
        if self.ide_scan_in <= 0:
            self.ide_scan_in = IDE_POLL
            self._check_apps(now)
            self._check_ide()

        self.front_in -= dt
        if self.front_in <= 0:
            self.front_in = FRONT_POLL
            self.front = apps.by_window(*commands.active_window())

        self.claude_in -= dt
        if self.claude_in <= 0:
            self.claude_in = CLAUDE_POLL
            if config.get("watch_claude"):
                self._check_claude(now)

        self.tabs_in -= dt
        if self.tabs_in <= 0:
            self.tabs_in = TABS_POLL
            if config.get("watch_tabs"):
                self._check_tabs()

        self.health_in -= dt
        if self.health_in <= 0:
            self.health_in = HEALTH_POLL
            if config.get("watch_hardware"):
                self._health_news(self.health.check_local(now))

        self.remote_in -= dt
        if self.remote_in <= 0:
            self.remote_in = REMOTE_POLL
            if config.get("watch_hardware"):
                # ssh blocks for seconds; the answer is judged on the GTK thread
                self.health.fetch_remote(
                    lambda readings: GLib.idle_add(self._remote_news, readings))

        self._check_notes(now)
        self._maybe_glance(now)

        cx, cy = self._cursor()

        if self.dragging:
            self.x = cx - self.dragging[0]
            self.y = cy - self.dragging[1]
            self.bed.set_hover(self.bed.contains(cx, cy))
        else:
            self.plan_in -= dt
            if self.target is not None:
                tx, ty = self.target
                dx, dy = tx - self.x, ty - self.y
                gap = math.hypot(dx, dy)
                step = self.speed * dt
                if gap <= step or gap < 1e-6:
                    self.x, self.y = tx, ty
                    self.target, self.speed = None, 0.0
                    # vanishing-act runs take precedence over idle
                    ph = getattr(self, "_vanish_phase", None)
                    if ph == "to_corner":
                        self._vanish_at_corner()
                    elif ph == "returning":
                        self._vanish_origin = None
                        self._vanish_pending = False
                        self._vanish_phase = None
                        self.anim.set_state("idle")
                        self.plan_in = self.rng.uniform(1.0, 3.0)
                        try:
                            self._say_dressed()
                        except Exception:
                            pass
                        self._schedule_next_vanish()
                    else:
                        self.anim.set_state("idle")
                        self.plan_in = self.rng.uniform(1.0, 3.0)
                else:
                    self.x += dx / gap * step
                    self.y += dy / gap * step
                    if abs(dx) > 1.0:
                        # the art faces left — the tail sits to the right of
                        # the body — so walking right is the mirrored case
                        self.facing = -1 if dx > 0 else 1
            elif self.plan_in <= 0:
                self._plan()

            # reactions to the pointer
            head_y = self.y + self.bounds[1] + self.height_px * 0.3
            dist = math.hypot(cx - self.x, cy - head_y)
            moved = math.hypot(cx - self.last_ptr[0], cy - self.last_ptr[1]) / max(dt, 1e-3)
            if (self.anim.state not in ("sleep", "drag")
                    and not self.anim.busy and not self._vanish_pending):
                if dist < 260 and moved > 1400 and self._cool("cursorFast", 12):
                    self.anim.react("cursorFast")
                elif dist < 150 and self._cool("cursorNear", 7):
                    self.anim.react("cursorNear")

        # never wander off the desktop
        self.x = max(self.roam[0], min(self.roam[2], self.x))
        self.y = max(self.roam[1], min(self.roam[3], self.y))

        self.last_ptr = (cx, cy)

        # eyes follow the pointer
        eye_y = self.y + self.bounds[1] + self.height_px * 0.25
        gx = max(-1.0, min(1.0, (cx - self.x) / 400.0)) * self.facing
        gy = max(-1.0, min(1.0, (cy - eye_y) / 300.0))
        self.gaze = (self.gaze[0] + (gx - self.gaze[0]) * 0.08,
                     self.gaze[1] + (gy - self.gaze[1]) * 0.08)

        if self.said and not self.streaming and now > self.bubble_until:
            self.said = ""

        self.pose = self.anim.update(dt)
        render.apply_gaze(self.skin, self.pose, self.gaze[0], -self.gaze[1])

        self.move(int(self.x - self.origin[0]), int(self.y - self.origin[1]))
        self.queue_draw()
        return True

    # -- input ---------------------------------------------------------
    def _on_press(self, _w, ev):
        if ev.button == 3:
            self._menu(ev)
            return True
        if ev.button == 1:
            cx, cy = self._cursor()
            self.dragging = (cx - self.x, cy - self.y)
            self.grab_from = (self.x, self.y)
            self.target, self.speed = None, 0.0
            self.anim.set_state("drag")
        return True

    def _on_release(self, _w, ev):
        if ev.button == 1 and self.dragging:
            self.dragging = None
            self.bed.set_hover(False)
            cx, cy = self._cursor()
            if self.bed.contains(cx, cy):
                self.go_to_bed()
            elif math.dist((self.x, self.y), self.grab_from) < 12:
                self.anim.set_state("idle")
                self.anim.react("poke")
                self.plan_in = 2.0
                self.ask(self._poked_prompt())
            else:
                self.anim.set_state("idle")   # stays wherever it was put down
                self.plan_in = self.rng.uniform(1.5, 4.0)
        return True

    def _on_motion(self, *_):
        return True

    def _menu(self, ev):
        menu = Gtk.Menu()
        skins = Gtk.MenuItem(label="Скин")
        sub = Gtk.Menu()
        for d in sorted((ASSETS / "skins").iterdir()):
            it = Gtk.MenuItem(label=d.name.replace("_cat", "").replace("_", " "))
            it.connect("activate", lambda _i, n=d.name: self._switch(n))
            sub.append(it)
        skins.set_submenu(sub)
        menu.append(skins)
        available = chat.available() or ["(ollama не отвечает)"]
        item = Gtk.MenuItem(label="Модель")
        sub = Gtk.Menu()
        for name in available:
            it = Gtk.CheckMenuItem(label=name)
            it.set_active(name == self.model)
            it.connect("toggled", self._model_chosen, name)
            sub.append(it)
        item.set_submenu(sub)
        menu.append(item)
        talk = Gtk.MenuItem(label="Поговорить…")
        talk.connect("activate", lambda *_: self._prompt_window())
        menu.append(talk)
        poof = Gtk.MenuItem(label="Исчезнуть")
        poof.set_sensitive(not self._vanish_pending and not self.in_bed)
        poof.connect("activate", lambda *_: self._trigger_vanish(manual=True))
        menu.append(poof)
        eye = Gtk.CheckMenuItem(label="Смотреть на экран")
        eye.set_active(self.watch_screen)
        eye.set_sensitive(bool(self.model_vision))
        eye.connect("toggled", self._watch_toggled)
        menu.append(eye)
        setup = Gtk.MenuItem(label="Настройки…")
        setup.connect("activate", lambda *_: self._settings_window())
        menu.append(setup)
        machines = Gtk.MenuItem(label="Машины")
        machines.connect("activate", lambda *_: self._show_machines())
        menu.append(machines)
        diary = Gtk.MenuItem(label="Открыть дневник")
        diary.connect("activate", lambda *_: Gtk.show_uri_on_window(
            None, f"file://{memory.JOURNAL}", Gdk.CURRENT_TIME))
        menu.append(diary)
        cards = Gtk.MenuItem(label="Открыть картотеку")
        cards.connect("activate", lambda *_: Gtk.show_uri_on_window(
            None, f"file://{knowledge.KNOWLEDGE}", Gdk.CURRENT_TIME))
        menu.append(cards)
        nap = Gtk.MenuItem(label="В лежанку")
        nap.set_sensitive(self.bed.present)
        nap.connect("activate", lambda *_: self.go_to_bed())
        menu.append(nap)
        quit_it = Gtk.MenuItem(label="Выход")
        quit_it.connect("activate", lambda *_: Gtk.main_quit())
        menu.append(quit_it)
        menu.show_all()
        menu.popup_at_pointer(ev)

    def _settings_window(self):
        """One window at a time — a second copy would fight the first over the file."""
        if getattr(self, "_prefs", None):
            self._prefs.present()
            return
        self._prefs = prefs.Window(self)
        self._prefs.connect("destroy", lambda *_: setattr(self, "_prefs", None))

    def _show_machines(self):
        """Whatever the last poll saw, straight in the balloon — no model.

        Also the only place the ssh side is visible at all, so a host that is
        misconfigured says so here instead of silently never reporting.
        """
        text = self.health.summary(limit=2) or "ещё не смотрел"
        if not self.health.hosts:
            text += f"\n(другие машины: пусто — {health.HOSTS})"
        self.say(text, secs=14)

    def _start_voice(self):
        """Bring up dictation, commands and the wake word.

        Kept out of __init__ because it loads a Whisper model, and the cat
        should already be on screen while that happens.
        """
        if self.ears is not None:
            return False
        why = ears_mod.available() or keys.available()
        if why:
            self.say(why, secs=14)
            return False
        from . import listen
        self.ears = listen.Listener(self)
        if self.ears.error:
            self.say(self.ears.error, secs=12)
        return False

    def _stop_voice(self):
        if self.ears is not None:
            self.ears.stop()
            self.ears = None

    def apply_settings(self):
        """Called by the settings window: take what can be taken without a restart."""
        config.load(reload=True)
        self.gap = float(config.get("chatter_gap"))
        self.watch_screen = bool(config.get("watch_screen")) and bool(self.model_vision)
        if config.get("watch_notifications"):
            self.notes.start()
        else:
            self.notes.stop()
        if config.get("voice_enabled"):
            self._start_voice()
        else:
            self._stop_voice()
        chosen = config.get("model")
        if chosen and chosen != self.model:
            self.model, self.history = chosen, []   # a new model inherits no mood

    def _watch_toggled(self, item):
        self.watch_screen = item.get_active()
        config.save({"watch_screen": self.watch_screen})
        self.say("подглядываю за экраном" if self.watch_screen else "не смотрю на экран")

    def _model_chosen(self, item, name):
        if not item.get_active() or ":" not in name or name == self.model:
            return
        self.model = name
        self.history.clear()              # a new model inherits no mood
        config.save({"model": name})
        self.say(f"думаю теперь через {name}")

    def _switch(self, name):
        config.save({"skin": name})
        self.load_skin(name)
        self.foot = self.bounds[3]
        self._shape()
        self.anim.react("poke")

    # -- the vanishing act -------------------------------------------------
    def _schedule_next_vanish(self):
        """Book the next random disappearing act — strictly 5-7 min as requested."""
        if self._vanish_next_timer:
            GLib.source_remove(self._vanish_next_timer)
        # 5-7 minutes between tricks, jittered per request; config kept for enable/disable
        secs = int(self.rng.uniform(300, 420))
        self._next_vanish_ts = time.monotonic() + secs
        self._vanish_next_timer = GLib.timeout_add_seconds(secs, self._vanish_due)
        return False

    def _vanish_due(self):
        """The booked moment arrived — vanish, unless life got in the way."""
        self._vanish_next_timer = None
        if (not config.get("vanish_enabled") or self.in_bed
                or self.prompting or self._vanish_pending):
            self._schedule_next_vanish()     # try again after another interval
            return False
        if not self._trigger_vanish():
            self._schedule_next_vanish()
        return False

    def _trigger_vanish(self, manual=False):
        """Run to the nearest corner, then fade out, change skins unseen, and book the walk back.

        manual=True is the menu item: an explicit request works even when the
        random event is switched off, but never twice over or from bed.
        The cat first runs to the closest roam corner (as if slipping away behind
        the edge), then plays vanish → hidden.
        """
        if self._vanish_pending or self.in_bed or self.prompting:
            return False
        if not manual and not config.get("vanish_enabled"):
            return False
        # nearest roam corner by feet position
        x0, y0, x1, y1 = self.roam
        corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
        cx, cy = min(corners, key=lambda p: math.hypot(p[0] - self.x, p[1] - self.y))
        self._vanish_origin = (self.x, self.y)
        self._vanish_pending = True
        self._vanish_phase = "to_corner"
        self._vanish_corner = (cx, cy)
        self.target = (cx, cy)
        self.speed = RUN_SPEED * self.height_px / 120.0
        self.plan_in = math.inf              # an absent cat plans nothing while trick is in flight
        # face the corner before running
        if abs(cx - self.x) > 1.0:
            self.facing = -1 if cx > self.x else 1
        self.anim.set_state("run")
        return True

    def _vanish_at_corner(self):
        """Reached the corner — now actually fade out."""
        if not self._vanish_pending or self._vanish_phase != "to_corner":
            return False
        self._vanish_phase = "vanishing"
        self.target, self.speed = None, 0.0
        self.anim.set_state("vanish")
        self._vanish_hide_timer = GLib.timeout_add(VANISH_HIDE_MS, self._vanished)
        return False

    def _vanished(self):
        """Fully faded out: unmap the window and pick a random cosmetic."""
        self._vanish_hide_timer = None
        self._vanish_phase = "hidden"
        self.hide()
        self.anim.set_state("hidden")
        # equip one random cosmetic from assets/cosmetics while hidden
        self._equip_random_cosmetic()
        self._vanish_return_timer = GLib.timeout_add_seconds(
            self.rng.randint(*VANISH_AWAY), self._return_from_vanish)
        return False

    def _return_from_vanish(self):
        """Step back onto the desktop, fading in as somebody new."""
        self._vanish_return_timer = None
        if not self._vanish_pending or self.in_bed:
            self._cancel_vanish()            # cancelled meanwhile (bed, quit)
            return False
        self._vanish_phase = "appearing"
        self.show_all()
        # appear near the corner where it vanished, but slightly inset so it is fully visible
        self._shape()
        self.anim.set_state("appear")
        self._vanish_appear_timer = GLib.timeout_add(
            VANISH_SHOW_MS, self._back_from_vanish)
        return False

    def _back_from_vanish(self):
        """Appear done — walk back to where the trick started, then resume."""
        self._vanish_appear_timer = None
        if self._vanish_origin is not None:
            ox, oy = self._vanish_origin
            # clamp origin inside roam in case workarea changed while hidden
            ox = max(self.roam[0], min(self.roam[2], ox))
            oy = max(self.roam[1], min(self.roam[3], oy))
            if math.hypot(ox - self.x, oy - self.y) > 4:
                self._vanish_phase = "returning"
                self.target = (ox, oy)
                self.speed = RUN_SPEED * self.height_px / 120.0
                if abs(ox - self.x) > 1.0:
                    self.facing = -1 if ox > self.x else 1
                self.anim.set_state("run")
                return False
        # already at origin or no origin saved
        self._vanish_origin = None
        self._vanish_pending = False
        self._vanish_phase = None
        self.anim.set_state("idle")
        self.plan_in = self.rng.uniform(1.0, 3.0)
        try:
            self._say_dressed()
        except Exception:
            pass
        self._schedule_next_vanish()
        return False

    def _cancel_vanish(self):
        """Drop any trick in flight and the booking; safe to call twice.

        Returns True when something was actually cancelled, so callers can
        rebook only then.
        """
        was = self._vanish_pending or any(
            getattr(self, attr) for attr in ("_vanish_next_timer",
                                             "_vanish_hide_timer",
                                             "_vanish_return_timer",
                                             "_vanish_appear_timer"))
        for attr in ("_vanish_next_timer", "_vanish_hide_timer",
                     "_vanish_return_timer", "_vanish_appear_timer"):
            src = getattr(self, attr)
            if src:
                GLib.source_remove(src)
                setattr(self, attr, None)
        self._vanish_pending = False
        self._vanish_phase = None
        return was

    def _equip_random_cosmetic(self):
        """Pick one random cosmetic from assets/cosmetics and remember it for 5 min."""
        # cancel previous strip timer
        if self._cosmetic_timer is not None:
            try:
                GLib.source_remove(self._cosmetic_timer)
            except Exception:
                pass
            self._cosmetic_timer = None
        try:
            dirs = [d for d in COSMETICS_DIR.iterdir() if d.is_dir()]
            if not dirs:
                return
            d = self.rng.choice(dirs)
            meta = json.loads((d / "cosmetic.json").read_text(encoding="utf8"))
            tex = meta.get("texture") or "texture.png"
            p = d / tex
            surf = cairo.ImageSurface.create_from_png(str(p)) if p.exists() else None
            if surf is None:
                return
            self._cosmetic = meta
            self._cosmetic_surf = surf
            self._cosmetic_slot = meta.get("slot") or "head"
            # auto-strip in 5 minutes with a line
            self._cosmetic_timer = GLib.timeout_add_seconds(300, self._strip_cosmetic)
        except Exception:
            self._cosmetic = None
            self._cosmetic_surf = None
            self._cosmetic_slot = None

    def _clear_cosmetic(self):
        if self._cosmetic_timer is not None:
            try:
                GLib.source_remove(self._cosmetic_timer)
            except Exception:
                pass
            self._cosmetic_timer = None
        self._cosmetic = None
        self._cosmetic_surf = None
        self._cosmetic_slot = None
        self.queue_draw()

    def _strip_cosmetic(self):
        """Called 5 min after equipping: say something, take it off."""
        self._cosmetic_timer = None
        if self._cosmetic is None:
            return False
        name = ""
        try:
            name = self._cosmetic.get("displayName", {}).get("ru") or self._cosmetic.get("id") or ""
        except Exception:
            pass
        self._clear_cosmetic()
        # a short remark so the undressing is not silent
        try:
            line = self.rng.choice([
                f"снял {name} — жарко в нём",
                "переоделся обратно",
                "так, хватит маскарада",
                f"{name} — прикольно, но пора без него",
                "снял костюмчик",
            ]) if name else self.rng.choice(["снял костюмчик", "переоделся", "так, хватит маскарада"])
            self.say(line, secs=6)
        except Exception:
            pass
        return False

    def _say_dressed(self):
        if self._cosmetic is None:
            return
        name = ""
        try:
            name = self._cosmetic.get("displayName", {}).get("ru") or self._cosmetic.get("id") or ""
        except Exception:
            pass
        try:
            line = self.rng.choice([
                f"как тебе мой {name}?",
                f"смотри, надел {name}!",
                f"новый образ — {name}",
                "ну как я выгляжу?",
                f"зацени {name}!",
                "мне идёт?",
            ]) if name else self.rng.choice(["ну как я выгляжу?", "новый образ!", "зацени!"])
            self.say(line, secs=7)
        except Exception:
            pass

    # -- bed ------------------------------------------------------------
    def go_to_bed(self, grace=False):
        """Curl up in the basket: the roaming window goes away entirely.

        grace=True is the boot case: the panel applet may not have announced
        itself yet, so the cat waits a little and walks out onto the desktop if
        no basket ever appears. Otherwise it would sit invisible with nothing
        to click.
        """
        # a trick in flight would fight the basket over who hides the window
        if self._cancel_vanish() and config.get("vanish_enabled"):
            self._schedule_next_vanish()
        if self.prompting:
            self._end_prompt(None)     # asleep with the keyboard grabbed is a trap
        self._stop_typing()
        if self.streaming and self.abort:
            self.abort()
        self.turn += 1
        self.streaming, self.said = False, ""
        self.anim.talk(False)
        self.target, self.speed = None, 0.0
        self.in_bed = True
        self.bed.set_occupied(True)
        self.hide()
        if grace:
            GLib.timeout_add(int(BED_GRACE * 1000), self._bed_or_bust)

    def _bed_or_bust(self):
        """No basket in any panel — better a cat on the desktop than none."""
        if self.in_bed and not self.bed.present:
            self.wake_up()
        return False

    def wake_up(self):
        """Clicked in the basket — step back out next to it."""
        if not self.in_bed:
            return
        # if the cat was mid-vanish, that trick ends here: it is visible now
        if self._cancel_vanish() and config.get("vanish_enabled"):
            self._schedule_next_vanish()
        self.bed.set_occupied(False)
        self.in_bed = False
        spot = self.bed.slot()
        if spot:
            self.x = max(self.roam[0], min(self.roam[2], spot[0]))
            self.y = max(self.roam[1], min(self.roam[3], spot[1]))
        self.anim.set_state("idle")
        self.anim.play_once("break_stretch")
        self.plan_in = 3.0
        self.last_t = time.monotonic()
        self.show_all()
        self._shape()

    # -- is the owner still there ----------------------------------------
    def _idle_seconds(self) -> float:
        """Seconds since the last keypress or mouse move, straight off KDE."""
        try:
            if self._idle_proxy is None:
                self._idle_proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                    "org.kde.screensaver", "/ScreenSaver",
                    "org.freedesktop.ScreenSaver", None)
            v = self._idle_proxy.call_sync("GetSessionIdleTime", None,
                                           Gio.DBusCallFlags.NONE, 2000, None)
            return v.unpack()[0] / 1000.0
        except Exception:
            return 0.0

    def _check_presence(self, now):
        """The heavy model only runs when nobody is here to be slowed down.

        The cat asks first rather than assuming: if the answer is silence for
        long enough, the owner really has gone, and the deep pass is free.
        """
        self.idle_s = self._idle_seconds()

        if self.idle_s < BACK_AT:
            if self.away:
                self.away = False
                self.break_now = True     # back at the desk: anything held can go now
            self.asked_at = 0.0
            return
        if self.away or self.in_bed:
            return
        if not self.asked_at and self.idle_s > IDLE_ASK:
            self.asked_at = now
            self.ask("Хозяина давно не слышно. Спроси, тут ли он ещё, одной фразой.")
        elif self.asked_at and self.idle_s > IDLE_ASK + ASK_WAIT:
            self.away = True
            # the empty room used to be when a second, stronger model read the
            # whole project and wrote up findings. That is gone: an animal
            # notices and remembers, it does not file reports. What is left is
            # the remembering — cheap, and still better done unobserved.
            self._harvest()

    # -- long memory: facts and squeezed weeks -------------------------------
    def _harvest(self):
        """Turn the day into facts worth keeping, then squeeze old weeks.

        Both run here, in the empty room, for the same reason the project
        analysis does: the strong model is pinned to the CPU, and doing this
        per reply would make every reply wait for it.
        """
        if self.harvesting or not self.model:
            return
        day = memory.recent(budget=2600, days=1)
        if len(day.strip()) < 120:
            self._digest()          # nothing said today, but weeks may be due
            return
        self.harvesting = True
        msgs = [
            {"role": "system", "content":
             "Ты ведёшь картотеку о хозяине. Из записей за день выбери то, "
             "что стоит помнить долго: его слова, решения, предпочтения, "
             "людей, планы.\n"
             "Формат строго построчно: тема | факт\n"
             "Тема — одно-два слова (человек, проект, занятие). Факт — одно "
             "короткое предложение.\n"
             "СТРОГО: только то, что прямо есть в записях. Ничего не "
             "додумывай. Сиюминутное (какая песня играла, сколько места на "
             "диске) не бери — оно неинтересно через неделю. "
             "Максимум 6 строк, можно меньше или ни одной."},
            {"role": "user", "content": day},
        ]
        chat.stream(
            self.model, msgs, lambda _c: None,
            lambda full, err: GLib.idle_add(self._harvested, full, err),
            options={"temperature": 0.2, "num_predict": 300},
        )

    def _harvested(self, full, err):
        self.harvesting = False
        kept = 0
        for line in (full or "").splitlines():
            if "|" not in line:
                continue
            subject, _, text = line.partition("|")
            subject = subject.strip(" -*·#").strip()
            if subject and text.strip() and knowledge.add(subject, text.strip()):
                kept += 1
        if kept:
            memory.write("картотека", f"запомнил {kept} фактов")
        self._digest()
        return False

    def _digest(self):
        """Squeeze one finished week into a few sentences. One per pass."""
        if self.digesting:
            return
        due = knowledge.undigested()
        if not due:
            return
        key, text = due[0]
        self.digesting = key
        msgs = [
            {"role": "system", "content":
             "Перед тобой записи кота за неделю. Сожми их в 3-5 предложений: "
             "чем хозяин занимался, что было заметного, кто ему писал. "
             "Пиши по-русски, прошедшим временем, без нумерации и заголовков. "
             "Только то, что есть в записях."},
            {"role": "user", "content": text[:9000]},
        ]
        chat.stream(
            self.model, msgs, lambda _c: None,
            lambda full, err: GLib.idle_add(self._digested, key, full, err),
            options={"temperature": 0.3, "num_predict": 260},
        )

    def _digested(self, key, full, err):
        self.digesting = None
        if not err and full and full.strip():
            knowledge.save_digest(key, full.strip())
            memory.write("сжал неделю", f"{key}: {full.strip()[:200]}")
        return False

    # -- which programs are open -----------------------------------------
    def _check_apps(self, now):
        """Notice programs starting and stopping, and have an opinion.

        Closing matters as much as opening: the cat knows how long the thing
        was up, which is the difference between "наигрался" and "и трёх минут
        не выдержал".
        """
        now_apps = apps.running()
        started, stopped = now_apps - self.apps, self.apps - now_apps
        self.apps = now_apps
        for key in started:
            self.app_since[key] = now
        gone = [(key, now - self.app_since.pop(key, now)) for key in stopped]
        if not self.apps_known:
            self.apps_known = True
            return                       # what was already open is not news
        if not (started or gone) or self.in_bed:
            return
        if started:
            self.screen_due = 0.0        # a new program is worth a look
        if gone:
            self.break_now = True        # closing something is a natural pause
        if not self._cool("apps", APP_COOLDOWN):
            return

        lines = []
        if started:
            lines.append("Только что открылось:\n" + apps.describe(started))
        for key, ran in gone:
            app = apps.BY_KEY.get(key)
            if app:
                lines.append(f"Закрылось: {app.name} ({app.meaning}), "
                             f"проработало {claude_mod.spell(ran)}")
        self.ask("\n".join(lines) + "\nСкажи что-нибудь по этому поводу — своё, "
                 "кошачье. Про прошлые темы не вспоминай.")

    # -- browser ----------------------------------------------------------
    def _check_tabs(self):
        """What is open in the browser: context always, a remark occasionally."""
        if self.in_bed:
            return
        try:
            found = tabs.open_tabs()
        except Exception:
            return                       # a session file caught mid-write
        self.tabs = found
        now_domains = tabs.domains(found)
        # replaced, not merged: a site closed and opened again is news again
        fresh = now_domains - self.domains_seen
        self.domains_seen = now_domains
        if not self.tabs_known:
            self.tabs_known = True
            return
        if not fresh or self.rng.random() > 0.5:
            return
        if not self._cool("tabs", TABS_COOLDOWN):
            return
        opened = [t for t in found if t.domain in fresh]
        self.ask(f"Хозяин открыл в браузере:\n{tabs.describe(opened, limit=4)}\n"
                 f"Скажи что-нибудь про это.")

    # -- what Claude Code is up to ----------------------------------------
    def _check_claude(self, now):
        """The one interruption that is always welcome: it is waiting for you."""
        event = self.claude.poll(now)
        if event is None or self.in_bed:
            return
        kind, text = event
        self.break_now = True            # a finished turn is a fair moment
        if self.away:
            memory.write("клод", text)
            return
        self.anim.react("cursorNear")    # ears up before the mouth opens
        hint = ("Позови хозяина." if kind == "question"
                else "Скажи как бы между делом.")
        self.ask(f"{text}. {hint} Одной фразой, по-кошачьи, но так, чтобы было "
                 f"понятно, что речь про Клода, а не про тебя.",
                 spontaneous=False)

    # -- hardware, here and elsewhere -------------------------------------
    def _remote_news(self, readings):
        now = time.monotonic()
        found = []
        for r in readings:
            found += self.health.news(r, now)
        return self._health_news(found)

    def _health_news(self, found):
        """Disk, memory, load — locally and on the machines in hosts.json."""
        for _key, text in found:
            memory.write("железо", text)
        if not found or self.in_bed or self.away:
            return False                 # written down; nobody here to tell
        self.ask("Ты заметил вот что: " + "; ".join(t for _k, t in found[:2])
                 + ".\nСкажи хозяину — коротко и по-кошачьи, но чтобы из фразы "
                   "было ясно, что именно случилось и где.",
                 spontaneous=False)      # a full disk is worth saying out loud
        return False

    # -- notifications other programs sent ---------------------------------
    def _check_notes(self, now):
        """Hold what came in, hand it over at a pause.

        Interrupting someone mid-line with "тебе написали" is what the panel
        popup already does badly. The cat waits for the typing to stop, or for
        whatever they were waiting on to finish — unless it is marked urgent,
        or it has been held so long that quiet has stopped being polite.
        """
        if self.in_bed or self.streaming or self.away or not self.notes.pending:
            return
        urgent = self.notes.has_urgent()
        pause = (self.break_now or urgent or self.idle_s >= FLOW_IDLE
                 or self.notes.oldest_age(now) > NOTE_HOLD)
        # something marked critical does not queue behind the polite gap
        if not pause or not (self._cool("notes", NOTE_GAP) or urgent):
            return
        self.break_now = False
        got = self.notes.drain()
        doing = ""
        if self.project and self.project.open_file and self.idle_s < FLOW_IDLE:
            doing = (f" Хозяин прямо сейчас пишет код в файле "
                     f"{self.project.open_file} — предложи глянуть, когда допишет.")
        self.ask(
            "Пока хозяин работал, пришли уведомления:\n"
            + "\n".join(f"— {n}" for n in got[:4])
            + f"\nПередай ему, от кого и о чём, одной-двумя фразами.{doing}",
            spontaneous=False,
        )

    # -- glancing at the screen ------------------------------------------
    def _grab_screen(self):
        """Base64 JPEG of the desktop. Held in memory only — never written out."""
        root = Gdk.get_default_root_window()
        w, h = root.get_width(), root.get_height()
        pb = Gdk.pixbuf_get_from_window(root, 0, 0, w, h)
        if pb is None or w <= 0:
            return None
        small = pb.scale_simple(SCREEN_WIDTH, max(1, int(h * SCREEN_WIDTH / w)),
                                GdkPixbuf.InterpType.BILINEAR)
        ok, data = small.save_to_bufferv("jpeg", ["quality"], ["70"])
        return base64.b64encode(data).decode() if ok else None

    def _maybe_glance(self, now):
        """A glance is earned, not scheduled.

        The cat looks when something changed — a program started — or when a
        randomised stretch has passed with nothing happening. Either way it
        must be standing still, the machine must be quiet, and never more
        often than SCREEN_MIN.
        """
        if not self.watch_screen or self.in_bed or self.streaming \
                or self._vanish_pending:
            return
        if self.target is not None or self.anim.state == "sleep":
            return                              # busy walking, or asleep
        # half again as often while playing: most to look at, least to read
        pace = PLAY_GLANCE if self.playing() else 1.0
        if now - self.screen_at < SCREEN_MIN / pace or now < self.screen_due:
            return
        if self.machine_busy():
            return
        self.screen_at = now
        self.screen_due = now + self.rng.uniform(*SCREEN_IDLE) / pace
        self._observe_screen()

    def _observe_screen(self):
        """Look once, write it in the journal. This never reaches the balloon."""
        if not (self.watch_screen and self.model_vision) or self.machine_busy():
            return
        shot = self._grab_screen()
        if not shot:
            return
        chat.stream(
            self.model_vision,
            [{"role": "user",
              "content": "Опиши одним-двумя предложениями, чем сейчас занят "
                         "хозяин, судя по экрану. Без предисловий."
                         + (f" Запущены: {apps.names(self.apps)}." if self.apps else ""),
              "images": [shot]}],
            lambda _c: None,
            lambda full, err: GLib.idle_add(self._observed, full, err),
            options={"temperature": 0.5, "num_predict": 90},
        )

    def _observed(self, full, err):
        """The glance goes in the journal — and now usually out loud as well.

        It used to only ever be written down, which made the most interesting
        thing the cat knows the one thing it never mentioned.
        """
        if err or not full:
            return False
        seen = full.strip()
        memory.write("экран", seen)
        if config.get("comment_screen"):
            self.ask(f"Ты подсмотрел, чем занят хозяин: {seen}\n"
                     f"Скажи что-нибудь по этому поводу — своё, кошачье. "
                     f"Не пересказывай увиденное дословно.")
        return False

    # -- what the user is working on -------------------------------------
    def _check_ide(self):
        """Notice which project is open, and study a new one exactly once."""
        proj = project.current()
        if proj is None:
            self.project = None
            return
        if self.project and proj.path == self.project.path:
            self.project = proj          # same project, refreshed open file
            return
        self.project = proj
        # no analysis pass any more: an animal notices which project is open
        # and what file is in front of it, and that is the whole of what it
        # knows about the code
        memory.write("хозяин открыл", f"{proj.name} в {proj.ide}")

    def _remark_on_code(self):
        """What the cat has actually seen lately, said out loud."""
        p = self.project
        where = f" Сейчас открыт файл {p.open_file}." if p.open_file else ""
        seen = memory.recent(budget=700)
        browser = tabs.describe(self.tabs, limit=4)
        # the squeezed weeks are what "давно" is made of: without them the cat
        # only ever knows the last three days and nothing before that
        past = knowledge.history(budget=350)
        self.ask(
            f"Хозяин работает над проектом {p.name}.\n"
            + (f"Твои записи о происходящем:\n{seen}\n" if seen else "")
            + (f"Что было раньше:\n{past}\n" if past else "")
            + (f"В браузере открыто:\n{browser}\n" if browser else "")
            + f"{where} Скажи что-нибудь по этому поводу.",
        )

    # -- music ----------------------------------------------------------
    def _on_track(self, track):
        """Spotify started a new song."""
        if not config.get("watch_music"):
            return
        self.track = track
        self.anim.react("cursorNear")     # ears up: it always notices...
        if self.track_timer:
            GLib.source_remove(self.track_timer)
        self.track_timer = GLib.timeout_add(
            int(TRACK_SETTLE * 1000), self._track_settled, track.id
        )

    def _track_settled(self, track_id):
        """...but only comments on songs that were not skipped straight past."""
        self.track_timer = None
        t = self.track
        if not t or t.id != track_id or self.streaming or self.in_bed:
            return False
        if not self._cool("music", MUSIC_COOLDOWN):
            return False
        self.ask(
            f"В Spotify заиграло: {t.artist} — «{t.title}». "
            f"Скажи что-нибудь про эту музыку."
        )
        return False

    # -- conversation ---------------------------------------------------
    def _pick_model(self):
        """One model for everything the cat says and remembers.

        There used to be two: a fast one that talked and a strong one pinned to
        the CPU that read code while nobody was watching. That split bought
        analysis the cat no longer does, and cost a whole class of problems —
        two models fighting for memory, the fast one dropping from 6.0 to 2.2
        tokens a second whenever the other woke up.

        Preference order is deliberate: a heavily quantised model gets names
        wrong in a way that sounds like nonsense — it called Kanye West
        "Кенни" — so anything at four bits wins over an iq2 of the same weights.
        """
        names = chat.available()
        chosen = MODEL or config.get("model") or config.get("model_fast")
        if chosen and chosen in names:
            return chosen
        for pick in (lambda n: n.startswith("qwen3.8") and not n.endswith("-cpu")
                     and "iq2" not in n.lower(),
                     lambda n: "qwen" in n and "iq2" not in n.lower(),
                     lambda n: True):
            found = next((n for n in names if pick(n)), "")
            if found:
                return found
        return ""

    def say(self, text, secs=BUBBLE_LINGER):
        """Put a fixed line in the balloon; no model involved.

        Silent while a question is open: the balloon *is* the input field then,
        and painting over it wipes the question and whatever has been typed
        into it so far, without stopping the keystrokes from still arriving.
        """
        if self.prompting:
            return
        self._stop_typing()
        self.said, self.streaming = text, False
        self.bubble_until = time.monotonic() + secs

    # -- the balloon as something to type into ------------------------------
    def _stop_typing(self):
        if self.type_timer:
            GLib.source_remove(self.type_timer)
            self.type_timer = None

    def _type_blip(self):
        """One click of the typewriter. sounds.play_type_sound throttles
        itself (40 ms) and never blocks; this only honours the mute switch."""
        if config.get("type_sound"):
            sounds.play_type_sound()

    def type_out(self, text, on_done=None, cps=TYPE_CPS):
        """Reveal a line a character at a time, the way a thing that is
        thinking would type it.

        The text is fixed — no model is asked. It only *looks* composed,
        because a canned sentence appearing instantly reads as a dialog box,
        and the same sentence typed out reads as the cat saying it.
        """
        if self.prompting:
            return                               # a question is already open
        self._stop_typing()
        self.said, self.streaming = "", True     # streaming gives the caret
        self.bubble_until = math.inf             # ...and stops the timeout
        self.anim.set_state("interact")
        self.anim.talk(True)
        self.target, self.speed = None, 0.0
        self.plan_in = math.inf
        shown = [0]

        def tick():
            shown[0] += 1
            self.said = text[:shown[0]]
            self._type_blip()
            if shown[0] < len(text):
                return True
            self.type_timer = None
            self.streaming = False
            self.anim.talk(False)
            self.bubble_until = time.monotonic() + BUBBLE_LINGER
            self.plan_in = 2.0
            if on_done:
                on_done()
            return False

        self.type_timer = GLib.timeout_add(max(8, int(1000 / cps)), tick)

    def prompt(self, question, on_answer, secret=False):
        """Type a question, then take the answer in the balloon itself.

        The keyboard is grabbed rather than focused: the cat's window is a
        DOCK that refuses focus on purpose, and a grab is what lets it read
        keys without becoming a normal window. A grab that leaked would leave
        the whole session unable to type, so it is released on Enter, on
        Escape, on a timeout, and when the window goes away.
        """
        if self.prompting:
            # a second question over the first would strand the first one's
            # callback forever, and the owner would be typing into whichever
            # of them happened to win
            return
        def begin():
            self.prompting = {"q": question, "buf": "", "secret": secret,
                              "cb": on_answer}
            seat = Gdk.Display.get_default().get_default_seat()
            seat.grab(self.get_window(), Gdk.SeatCapabilities.KEYBOARD,
                      False, None, None, None, None)
            self.prompt_timer = GLib.timeout_add(
                int(PROMPT_TIMEOUT * 1000), lambda: self._end_prompt(None))
            self._draw_prompt()

        self.type_out(question, on_done=begin)

    def _draw_prompt(self):
        p = self.prompting
        if not p:
            return
        shown = "•" * len(p["buf"]) if p["secret"] else p["buf"]
        self.said = f"{p['q']}\n> {shown}"
        self.streaming = True            # the caret marks it as waiting
        self.bubble_until = math.inf

    def _end_prompt(self, answer):
        p, self.prompting = self.prompting, None
        if self.prompt_timer:
            GLib.source_remove(self.prompt_timer)
            self.prompt_timer = None
        try:
            Gdk.Display.get_default().get_default_seat().ungrab()
        except Exception:
            pass
        self.streaming = False
        self.bubble_until = time.monotonic() + BUBBLE_LINGER
        self.plan_in = 2.0
        if p and p["cb"]:
            p["cb"](answer)
        return False

    def _on_key(self, _w, ev):
        if not self.prompting:
            return False
        name = Gdk.keyval_name(ev.keyval)
        if name in ("Return", "KP_Enter"):
            self._end_prompt(self.prompting["buf"].strip())
        elif name == "Escape":
            self._end_prompt(None)
        elif name == "BackSpace":
            self.prompting["buf"] = self.prompting["buf"][:-1]
            self._draw_prompt()
        else:
            char = chr(Gdk.keyval_to_unicode(ev.keyval) or 0)
            if char and char.isprintable():
                self.prompting["buf"] += char
                self._draw_prompt()
        return True

    def _poked_prompt(self):
        hour = time.localtime().tm_hour
        when = ("глубокая ночь" if hour < 5 else "утро" if hour < 12
                else "день" if hour < 18 else "вечер")
        return f"Тебя потыкали пальцем. Сейчас {when}. Скажи что-нибудь."

    def playing(self) -> bool:
        """Is a game the thing in front of the owner right now?

        Not "is a game running" — the launcher sits in the background for
        hours. What matters is what they are actually looking at.
        """
        return bool(self.front and self.front.game)

    def machine_busy(self) -> bool:
        """Is the machine already working hard enough without the cat's help?

        Measured while the cat is not inferring, so this is the load from
        everything else — a build, a test run, a game. A game is the exception:
        it pins the processor for as long as it is open, and the old ceiling
        turned that into an evening of silence.
        """
        return self.cpu_busy > (PLAY_CEILING if self.playing() else CPU_CEILING)

    def ask(self, prompt, spontaneous=True, from_owner=False):
        """smart=True routes to the better quantisation — used for code.

        The good model is pinned to the CPU, where evaluating even a short
        prompt costs seconds. That is invisible for background work but awful
        when someone is watching the balloon, so anything interactive goes to
        the fast model regardless.

        spontaneous replies are dropped when the machine is busy; something the
        owner actually typed is never silently swallowed.
        """
        if self.prompting:
            # The cat asked something and is waiting for the answer — a code
            # from Telegram, a cloud password. A remark firing now used to
            # cancel that: the keyboard grab was dropped mid-typing and the
            # digits already entered were thrown away. Whatever this was, it
            # is less important than the question already on screen.
            return
        if self._vanish_pending:
            return      # the balloon is unmapped with the window; talk later
        now = time.monotonic()
        if spontaneous:
            if self.machine_busy() or self.away:
                return      # nobody is here to hear it, and the deep pass needs the cores
            if now < self.quiet_until:
                return      # something else just spoke; one thought at a time
        # important lines silence the chatter behind them too, not just each other
        self.quiet_until = now + self.gap
        if from_owner:
            # the one thing the journal never held: what the owner actually
            # said. Without it the nightly pass has no conversation to remember
            memory.write("хозяин сказал", prompt.strip()[:400])
        if self.streaming and self.abort:
            self.abort()          # cut the cat off mid-sentence, it will cope
        self.turn += 1
        model = self.model
        if not model:
            self.say("мур?.. ollama не отвечает")
            return
        # Everything that changes between replies goes in the *user* turn, and
        # never in the system one. ollama reuses the KV cache for whatever
        # prefix two requests share, so a mood or a memory glued onto the front
        # throws away the whole cache and costs two seconds before the cat has
        # said anything at all.
        known = knowledge.recall(prompt, limit=3)
        turn_text = prompt
        if known:
            # dated, so it can say "ты это на прошлой неделе говорил" instead
            # of repeating it back as if it were news
            turn_text += ("\n\nТы помнишь про хозяина:\n" + known
                          + "\nУпомяни это, только если оно к месту.")
        turn_text += chat.flavour(self.rng)
        # the history keeps the plain prompt: the mood was for this reply only,
        # and carrying old moods forward would make every turn contradict itself
        self.history = self.history[-6:] + [{"role": "user", "content": prompt}]
        msgs = ([{"role": "system", "content": chat.system_prompt()}]
                + self.history[:-1]
                + [{"role": "user", "content": turn_text}])
        self.said, self.streaming = "", True
        self.target, self.speed = None, 0.0
        self.plan_in = math.inf          # do not wander off mid-sentence
        self.anim.set_state("interact")
        # the mouth stays shut until there is a word to shape: a cat mouthing
        # silence for five seconds looks broken, not thoughtful
        turn = self.turn
        _, self.abort = chat.stream(
            model, msgs,
            lambda c: GLib.idle_add(self._token, turn, c),
            lambda full, err: GLib.idle_add(self._finished, turn, full, err),
            # 45 was not enough for the two sentences the character is allowed:
            # replies were being chopped mid-word about as often as not
            options={"temperature": 0.7, "num_predict": 110},
        )

    def _token(self, turn, chunk):
        """Runs on the GTK thread — chat.stream calls back from a worker."""
        if turn != self.turn:      # an abandoned reply must not leak into the new one
            return False
        if not self.said and chunk:
            self.anim.talk(True)   # first real word: only now does it open its mouth
        self.said += chunk
        self._type_blip()          # one click per token, throttled inside
        return False

    def _finished(self, turn, full, err):
        if turn != self.turn:
            return False
        self.streaming = False
        self.anim.talk(False)
        self.abort = None
        if err:
            self.said = f"…{err[:80]}"
        elif full:
            # the balloon holds whatever streamed in token by token, which is
            # the *untrimmed* text — chat.tidy may have rolled a truncated
            # reply back to the last finished sentence, so show that instead
            self.said = full
            self.history.append({"role": "assistant", "content": full})
            if config.get("remember_session"):
                memory.save_session(self.history, time.time())
        self.bubble_until = time.monotonic() + BUBBLE_LINGER
        self.plan_in = 2.0
        return False

    def _prompt_window(self):
        w = Gtk.Window(title="Сказать коту")
        w.set_keep_above(True)
        w.set_default_size(360, -1)
        w.set_position(Gtk.WindowPosition.MOUSE)
        entry = Gtk.Entry(placeholder_text="что сказать Мяумори…")
        entry.set_margin_top(10)
        entry.set_margin_bottom(10)
        entry.set_margin_start(10)
        entry.set_margin_end(10)

        def send(*_):
            text = entry.get_text().strip()
            w.destroy()
            if not text:
                return
            if self._telegram_command(text):
                return          # "ответь лонеру что…" is an errand, not a remark
            self.ask(text, spontaneous=False, from_owner=True)

        entry.connect("activate", send)
        w.connect("key-press-event",
                  lambda _w, e: w.destroy() if e.keyval == Gdk.KEY_Escape else None)
        w.add(entry)
        w.show_all()
        w.present()
        return w, entry

    # -- running an errand in Telegram -------------------------------------
    def _telegram_command(self, text) -> bool:
        """"ответь лонеру что…" — resolve, draft, show, then send.

        Returns True if the line was an errand and has been taken on, so the
        caller knows not to treat it as something said to the cat.
        """
        parsed = telegram.parse_command(text)
        if not parsed:
            return False
        if not config.get("watch_telegram"):
            self.say("телеграм выключен — включи в настройках")
            return True
        why = telegram.available()
        if why:
            self.say(why, secs=14)
            return True
        who, gist = parsed
        if self.tg is None:
            self.tg = telegram.Telegram()
        self.say(f"ищу {who} в телеграме…", secs=30)
        self.tg.dialogs(lambda peers, err: GLib.idle_add(
            self._tg_resolved, who, gist, peers, err))
        return True

    def _tg_resolved(self, who, gist, peers, err):
        if err:
            self.say(f"телеграм: {err[:70]}", secs=12)
            return False
        found = telegram.match(who, peers or [])
        if not found:
            self.say(f"не нашёл «{who}» среди диалогов")
            return False
        peer = found[0]
        if len(found) > 1:
            # several matches: say which one was picked, so a wrong guess is
            # visible before the message goes anywhere
            self.say(f"это {peer}? пишу ему", secs=10)
        # the last few lines give the model something to answer *to*
        self.tg.history(peer.id, lambda text, e: GLib.idle_add(
            self._tg_draft, peer, gist, text if not e else ""))
        return False

    def _tg_draft(self, peer, gist, history):
        msgs = [
            {"role": "system",
             "content": telegram.DRAFT_SYSTEM.format(who=peer.name or "человек")},
            {"role": "user",
             "content": (f"Последние сообщения в этом диалоге:\n{history}\n\n"
                         if history else "")
             + f"Хозяин просит передать: {gist}"},
        ]
        self.say(f"сочиняю письмо для {peer.name}…", secs=40)
        chat.stream(
            self.model, msgs, lambda _c: None,
            lambda full, err: GLib.idle_add(self._tg_drafted, peer, full, err),
            options={"temperature": 0.6, "num_predict": 120},
        )
        return False

    def _tg_drafted(self, peer, full, err):
        if err or not full:
            self.say(f"не сочинилось: {(err or 'пусто')[:60]}")
            return False
        text = full.strip().strip('"«»')
        if not config.get("telegram_confirm"):
            self._tg_send(peer, text)
            return False

        # the draft goes in the balloon, not into a form: there is nothing to
        # edit here, only a yes or a no
        self.anim.set_state("interact")
        self.say(f"набросал: «{text[:180]}» — отправлять?", secs=40)
        why = voice.available() if config.get("voice_confirm") else "голос выключен"
        if why:
            self._tg_buttons(peer, text, why)
        else:
            self.listening = True
            self.anim.react("cursorNear")          # ears up: it is listening
            self.target, self.speed = None, 0.0
            self.plan_in = math.inf                # do not wander off mid-question
            voice.ask(lambda verdict, heard, e: GLib.idle_add(
                self._tg_heard, peer, text, verdict, heard, e))
        return False

    def _tg_heard(self, peer, text, verdict, heard, err):
        """Anything that is not clearly a yes leaves the message unsent."""
        self.listening = False
        self.plan_in = 2.0
        if verdict is True:
            self._tg_send(peer, text)
        elif verdict is False:
            self.say("ладно, не отправляю")
        elif err:
            self.say(f"не расслышал ({err[:50]}) — не отправляю", secs=10)
            self._tg_buttons(peer, text, "")
        else:
            self.say(f"не понял «{heard[:40]}» — не отправляю", secs=10)
            self._tg_buttons(peer, text, "")
        return False

    def _tg_buttons(self, peer, text, why):
        """Fallback when there is no ear: the same question, with two buttons.

        Deliberately not an editor — the draft is shown, not offered for
        rewriting. Wrong draft means saying the errand again.
        """
        w = Gtk.Window(title="Отправить в телеграм?")
        w.set_keep_above(True)
        w.set_default_size(400, -1)
        w.set_position(Gtk.WindowPosition.CENTER)
        w.set_border_width(12)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        w.add(box)

        head = Gtk.Label(xalign=0)
        head.set_markup(f"кому: <b>{GLib.markup_escape_text(str(peer))}</b>")
        box.pack_start(head, False, False, 0)

        body = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
        box.pack_start(body, False, False, 0)

        if why:
            note = Gtk.Label(xalign=0, wrap=True)
            note.set_markup(f"<small><i>голосом не спросить: "
                            f"{GLib.markup_escape_text(why)}</i></small>")
            box.pack_start(note, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cancel = Gtk.Button(label="Не надо")
        cancel.connect("clicked", lambda *_: w.destroy())
        go = Gtk.Button(label="Отправить")
        go.get_style_context().add_class("suggested-action")
        go.connect("clicked", lambda *_: (w.destroy(), self._tg_send(peer, text)))
        row.pack_end(go, False, False, 0)
        row.pack_end(cancel, False, False, 0)
        box.pack_start(row, False, False, 0)
        w.connect("key-press-event",
                  lambda _w, e: w.destroy() if e.keyval == Gdk.KEY_Escape else None)
        w.show_all()
        w.present()

    def _tg_send(self, peer, text):
        self.tg.send(peer.id, text, lambda _r, err: GLib.idle_add(
            self._tg_sent, peer, err))

    def _tg_sent(self, peer, err):
        if err:
            self.say(f"не отправилось: {err[:70]}", secs=12)
        else:
            self.say(f"отправил {peer.name}", secs=8)
            memory.write("телеграм", f"написал {peer}")
        return False

    # -- paint ---------------------------------------------------------
    def _on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.translate(*self.origin)
        render.draw(cr, self.skin, self.textures, self.pose, self.height_px, self.facing)
        if getattr(self, "_cosmetic_surf", None) is not None:
            render.draw_cosmetic(cr, self.skin, self.pose, self.height_px, self.facing,
                                 self._cosmetic, self._cosmetic_surf)
        # Nothing is drawn until there is something to say. An empty balloon
        # with a blinking caret, hanging there for the seconds the model needs
        # to read the prompt, advertises exactly how slow it is; appearing at
        # the first real token instead reads as a cat that answered at once.
        if self.said:
            render.draw_bubble(cr, self.said, 0, self.bounds[1] - 8, caret=self.streaming)
        return False


def already_running() -> bool:
    """Is there a cat on this desktop already?

    There are now three things that can start one — the autostart entry, the
    basket in the panel when it notices none is running, and a person at a
    terminal — and they race. Two cats fight over the same D-Bus name and the
    same state file: the second steals the name, and the first goes on walking
    around unable to talk to the basket at all.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        answer = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (bed_mod.BUS,)), GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE, 2000, None)
        return bool(answer.unpack()[0])
    except Exception:
        return False        # no bus, no way to tell — better to start than not


def main():
    if already_running():
        print("Кот уже запущен.", file=sys.stderr)
        return
    cat = Cat()
    cat.show_all()
    if "--bed" in sys.argv or os.environ.get("MEWMORI_START_IN_BED") == "1":
        GLib.idle_add(cat.go_to_bed, True)      # asleep from boot
    # a killed process would otherwise leave the applet believing the cat is
    # still around, so unwind on the usual signals too
    for sig in (GLib.unix_signal_add,):
        sig(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda *_: Gtk.main_quit() or True)
        sig(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda *_: Gtk.main_quit() or True)
    try:
        Gtk.main()
    finally:
        cat._end_prompt(None)     # a leaked keyboard grab locks the whole session
        cat.bed.close()
        cat.notes.stop()          # otherwise dbus-monitor outlives the cat
        cat._stop_voice()         # ...and so would the keyboard listener
