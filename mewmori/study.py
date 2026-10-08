"""Окно с карточками: одна на экране, щелчок — переворот.

Drawn with cairo rather than assembled from widgets, for one reason: the flip
has to be a flip. A GtkStack cross-fade between two labels reads as a page
swapping out; squeezing the card to nothing on its vertical axis, swapping the
face at the moment it has no width, and letting it open again reads as a piece
of card turning over in your hand. That is the whole difference between a form
and a deck, and cairo is already how everything else here is drawn.

The deck, the schedule and the file live in cards.py; this only shows them.
"""
from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import cards, chat, config, render  # noqa: E402

CARD_W, CARD_H = 420, 250
FLIP_MS = 22            # ms per animation step
FLIP_STEPS = 9          # per half-turn; two halves make the whole flip

ASK_BACK = (
    "Переведи на русский одним-двумя словами, без пояснений, без кавычек, "
    "без точки. Только перевод.\n\nСлово: "
)


class Window(Gtk.Window):
    """Одна карточка, кнопки под ней и счётчик того, что осталось."""

    def __init__(self, cat):
        super().__init__(title="Карточки Мяумори")
        self.cat = cat
        self.set_keep_above(True)
        self.set_default_size(CARD_W + 40, CARD_H + 130)
        self.set_position(Gtk.WindowPosition.CENTER)
        render.ensure_font()

        self.queue = cards.due()
        self.card = None
        self.done = 0           # сколько ответов за эту сессию — для кота
        self.flipped = False
        self.turn = 0.0         # 1.0 face on, 0.0 edge on
        self._anim = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(14)
        self.add(box)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(CARD_W, CARD_H)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", lambda *_: self.flip())
        box.pack_start(self.area, True, True, 0)

        self.counter = Gtk.Label()
        box.pack_start(self.counter, False, False, 0)

        # Four buttons, each carrying what it will do. The interval is the
        # whole point of choosing between them, and a button that does not say
        # where it sends the card is a button you press at random.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      homogeneous=True)
        self.grade_buttons = {}
        for grade, label in cards.GRADES:
            button = Gtk.Button()
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            title = Gtk.Label(label=label)
            when = Gtk.Label()
            when.set_markup("<small> </small>")
            inner.pack_start(title, False, False, 0)
            inner.pack_start(when, False, False, 0)
            button.add(inner)
            button.connect("clicked", lambda _b, g=grade: self._answer(g))
            row.pack_start(button, True, True, 0)
            self.grade_buttons[grade] = (button, when)
        box.pack_start(row, False, False, 0)

        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                       homogeneous=True)
        new = Gtk.Button(label="+ слово")
        new.connect("clicked", self._new_card)
        every = Gtk.Button(label="Показать все")
        every.connect("clicked", self._show_all)
        row2.pack_start(new, True, True, 0)
        row2.pack_start(every, True, True, 0)
        box.pack_start(row2, False, False, 0)

        self.connect("key-press-event", self._key)
        self.connect("destroy", self._closed)
        self._next()
        self.show_all()

    # -- the deck ----------------------------------------------------------
    def _next(self):
        if not self.queue:
            # nothing due — but a study session should not just stop. Pull the
            # rest of the deck by soonest due and keep going; FSRS shrugs off an
            # early review. The queue refills endlessly this way.
            self.queue = cards.practice(exclude=self.card["front"] if self.card else "")
            self.ahead = bool(self.queue)
        self.card = self.queue.pop(0) if self.queue else None
        self.flipped, self.turn = False, 1.0
        total, ready, learned = cards.stats()
        if self.card:
            tail = " · наперёд" if getattr(self, "ahead", False) and not ready else ""
            self.counter.set_text(
                f"на сегодня {ready} · всего {total} · выучено {learned}{tail}")
        else:
            # only reachable when the deck is one card (excluded) or empty
            self.counter.set_text(
                f"на сегодня всё · всего {total}, выучено {learned}"
                if total else "карточек пока нет — нажми «+ слово»")
        ahead = cards.preview(self.card) if self.card else {}
        for grade, (button, when) in self.grade_buttons.items():
            button.set_sensitive(bool(self.card))
            when.set_markup(f"<small>{ahead.get(grade, ' ')}</small>")
        self.area.queue_draw()

    def _answer(self, grade):
        if not self.card:
            return
        cards.answer(self.card, grade)
        self.done += 1
        self._next()

    def _key(self, _w, ev):
        name = Gdk.keyval_name(ev.keyval)
        if name == "Escape":
            self.destroy()
        elif name in ("space", "Return"):
            self.flip()
        elif name in ("1", "2", "3", "4"):
            self._answer(int(name))
        return True

    # -- the flip ----------------------------------------------------------
    def flip(self):
        """Squeeze to the edge, turn the face over there, open out again."""
        if not self.card or self._anim is not None:
            return
        step = [0]

        def frame():
            step[0] += 1
            if step[0] >= FLIP_STEPS * 2:
                # checked before the swap, not after: at step 2*FLIP_STEPS the
                # phase arithmetic comes back round to "turn the face over"
                # and the card flipped itself straight back
                self.turn, self._anim = 1.0, None
                self.area.queue_draw()
                return False
            half, i = divmod(step[0], FLIP_STEPS)
            if half == 0:                       # closing towards the edge
                self.turn = math.cos(i / FLIP_STEPS * math.pi / 2)
            else:                               # opening on the other face
                if i == 0:
                    self.flipped = not self.flipped
                self.turn = math.sin(i / FLIP_STEPS * math.pi / 2)
            self.area.queue_draw()
            return True

        self._anim = GLib.timeout_add(FLIP_MS, frame)

    def _closed(self, _w):
        """Кот замечает, что занятие было. Молча закрытое окно — не занятие."""
        self._stop_anim()
        done, self.done = self.done, 0
        if done and hasattr(self.cat, "cards_done"):
            GLib.idle_add(self.cat.cards_done, done)

    def _stop_anim(self):
        if self._anim is not None:
            GLib.source_remove(self._anim)
            self._anim = None

    # -- drawing -----------------------------------------------------------
    def _draw(self, area, cr):
        w, h = area.get_allocated_width(), area.get_allocated_height()
        cr.set_source_rgb(0.10, 0.10, 0.13)
        cr.paint()
        if not self.card:
            self._text(cr, w / 2, h / 2, "мур?", 30, (0.5, 0.5, 0.55), w - 60)
            return
        # The face is drawn square-on into its own surface and only *then*
        # squeezed. Laying it out inside the scaled context instead made Pango
        # re-wrap on every frame — "serendi-pity" halfway through the turn —
        # because it measures in the transformed space it is handed.
        face = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        self._face(cairo.Context(face), w, h)
        cr.save()
        # squeezed about the middle, so the card turns on its own axis rather
        # than sliding towards one edge
        cr.translate(w / 2, 0)
        cr.scale(max(self.turn, 0.001), 1.0)
        cr.translate(-w / 2, 0)
        cr.set_source_surface(face, 0, 0)
        cr.paint()
        cr.restore()

    def _face(self, cr, w, h):
        pad, r = 14, 14
        x, y, cw, ch = pad, pad, w - 2 * pad, h - 2 * pad
        cr.new_path()
        for cx, cy, a0, a1 in ((x + r, y + r, math.pi, 1.5 * math.pi),
                               (x + cw - r, y + r, 1.5 * math.pi, 0),
                               (x + cw - r, y + ch - r, 0, 0.5 * math.pi),
                               (x + r, y + ch - r, 0.5 * math.pi, math.pi)):
            cr.arc(cx, cy, r, a0, a1)
        cr.close_path()
        back = self.flipped
        cr.set_source_rgb(*((0.16, 0.15, 0.19) if back else (0.96, 0.95, 0.92)))
        cr.fill_preserve()
        cr.set_source_rgba(0.55, 0.50, 0.40, 0.8)
        cr.set_line_width(2)
        cr.stroke()

        ink = (0.95, 0.94, 0.90) if back else (0.10, 0.09, 0.12)
        text = (self.card.get("back") or "…") if back else self.card["front"]
        note = self.card.get("note", "") if back else ""
        size = 40 if len(text) < 18 else 28 if len(text) < 34 else 21
        self._text(cr, w / 2, h / 2 - (12 if note else 0), text, size, ink, cw - 40)
        if note:
            self._text(cr, w / 2, h / 2 + 42, note, 15,
                       (ink[0], ink[1], ink[2], 0.65), cw - 40)
        # Точки — не коробка Лейтнера, а то, сколько слова сейчас в памяти:
        # вспоминаемость по кривой забывания, которая тает между показами.
        filled = round(cards.strength(self.card) * 5)
        self._text(cr, w / 2, h - 34, "•" * filled + "·" * (5 - filled),
                   14, (0.55, 0.52, 0.45), cw)
        if not back:
            self._text(cr, w / 2, h - 54, "щёлкни, чтобы перевернуть", 12,
                       (0.45, 0.43, 0.38), cw)

    def _text(self, cr, cx, cy, text, size, rgb, max_w):
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(
            Pango.FontDescription(f"{render.ensure_font()} {size}"))
        lay.set_width(int(max_w) * Pango.SCALE)
        lay.set_alignment(Pango.Alignment.CENTER)
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_text(text, -1)
        # Pango already centres inside the width it was given, so the box goes
        # at cx - max_w/2. Offsetting by the measured width centres it twice
        # and drifts every short line off to one side.
        _tw, th = lay.get_pixel_size()
        cr.set_source_rgba(*(rgb if len(rgb) == 4 else rgb + (1.0,)))
        cr.move_to(cx - int(max_w) / 2, cy - th / 2)
        PangoCairo.show_layout(cr, lay)

    # -- adding ------------------------------------------------------------
    def _new_card(self, _b):
        dialog = Gtk.Dialog(title="Новое слово", transient_for=self, modal=True)
        dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL,
                           "Добавить", Gtk.ResponseType.OK)
        grid = dialog.get_content_area()
        grid.set_spacing(6)
        grid.set_border_width(12)
        front = Gtk.Entry(placeholder_text="слово")
        back = Gtk.Entry(placeholder_text="перевод — пусто, и кот переведёт сам")
        note = Gtk.Entry(placeholder_text="пример или пометка, необязательно")
        for entry in (front, back, note):
            entry.set_activates_default(True)
            grid.add(entry)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.show_all()
        ok = dialog.run() == Gtk.ResponseType.OK
        word, meaning, hint = (front.get_text().strip(), back.get_text().strip(),
                               note.get_text().strip())
        dialog.destroy()
        if not ok or not word:
            return
        if meaning:
            self._store(word, meaning, hint)
        else:
            self.counter.set_text(f"спрашиваю у кота, что такое «{word}»…")
            self._translate(word, hint)

    def _translate(self, word, hint):
        """Empty back side: let the model fill it in, and take what comes.

        The card is created either way. A word the owner typed and then lost
        because ollama was not running is worse than a card with a blank back
        they can fix by hand.
        """
        def finished(full, err):
            GLib.idle_add(self._store, word,
                          (full or "").strip().strip('"«».') if not err else "",
                          hint)

        chat.stream(self.cat.model or config.get("model"),
                    [{"role": "user", "content": ASK_BACK + word}],
                    lambda _c: None, finished,
                    options={"temperature": 0.1, "num_predict": 24})

    def _store(self, word, meaning, hint):
        fresh = cards.add(word, meaning, hint)
        if fresh:
            # read back rather than assembling a card here: a word that went to
            # Anki is graded by id, and this is where that id comes from
            card = cards.find(cards.load(), word)
            if card:
                self.queue.append(card)
        self._next()
        if not fresh:
            self.counter.set_text(f"«{word}» уже есть в колоде")
        return False

    def _show_all(self, _b):
        """Ignore the schedule and go through the whole deck once."""
        self.queue = [c for c in cards.load()
                      if not self.card or c["front"] != self.card["front"]]
        self._next()


def open_window(cat):
    """Показать окно карточек (одно на всех)."""
    if getattr(cat, "_cards_window", None):
        cat._cards_window.present()
        return cat._cards_window
    win = Window(cat)
    cat._cards_window = win
    win.connect("destroy", lambda *_: setattr(cat, "_cards_window", None))
    return win
