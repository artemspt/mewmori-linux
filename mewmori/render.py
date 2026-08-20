"""Rig and speech balloon -> cairo. Drawing only, no windowing."""
from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402


def load_textures(skin) -> dict:
    """Every png in the skin dir, as a cairo surface."""
    return {f.name: cairo.ImageSurface.create_from_png(str(f)) for f in skin.dir.glob("*.png")}


def _matrices(skin, pose):
    """World matrix + inherited alpha per part, in rig units (y flipped to screen down)."""
    mats, alphas = {}, {}
    for p in skin.parts:
        d = pose.get(p.id, {})
        m = cairo.Matrix()
        m.translate(p.position[0] + d.get("x", 0.0), -(p.position[1] + d.get("y", 0.0)))
        m.rotate(-math.radians(d.get("rotation", 0.0)))
        s = d.get("scale", 0.0)
        m.scale(1.0 + s + d.get("scaleX", 0.0), 1.0 + s + d.get("scaleY", 0.0))
        a = max(0.0, min(1.0, 1.0 + d.get("alpha", 0.0)))
        if p.parent:
            m = m.multiply(mats[p.parent])
            a *= alphas[p.parent]
        mats[p.id], alphas[p.id] = m, a
    return mats, alphas


def draw(cr, skin, textures, pose, height_px, facing=1):
    """Paint the cat with its rig origin at the current cairo origin."""
    k = height_px / skin.reference_height
    cr.save()
    cr.scale(k * facing, k)
    mats, alphas = _matrices(skin, pose)
    for p in sorted(skin.parts, key=lambda p: p.z):
        name = pose.get(p.id, {}).get("texture") or p.texture
        surf = textures.get(name)
        if surf is None or alphas[p.id] <= 0.004:
            continue
        w, h = surf.get_width(), surf.get_height()
        tw, th = w / skin.ppu, h / skin.ppu
        cr.save()
        cr.transform(mats[p.id])
        cr.translate(-p.anchor[0] * tw, -(1.0 - p.anchor[1]) * th)
        cr.scale(1.0 / skin.ppu, 1.0 / skin.ppu)
        cr.set_source_surface(surf, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint_with_alpha(alphas[p.id])
        cr.restore()
    cr.restore()


def ink_box(surf):
    """Tight box around the non-transparent pixels of a texture, in its own pixels.

    The art sits inside a lot of empty padding, so the texture rectangle is useless
    for working out where the cat's feet actually are.
    """
    w, h, stride = surf.get_width(), surf.get_height(), surf.get_stride()
    buf = bytes(surf.get_data())
    x0, y0, x1, y1 = w, h, 0, 0
    for y in range(h):
        row = buf[y * stride + 3: y * stride + 3 + w * 4: 4]  # ARGB32 -> alpha byte
        left = len(row) - len(row.lstrip(b"\x00"))
        if left == len(row):
            continue
        x0, x1 = min(x0, left), max(x1, len(row.rstrip(b"\x00")))
        y0, y1 = min(y0, y), max(y1, y + 1)
    return (0, 0, w, h) if x0 >= x1 else (x0, y0, x1, y1)


def pose_bounds(skin, textures, height_px, pose=None):
    """(min_x, min_y, max_x, max_y) in px around the rig origin for a given pose."""
    k = height_px / skin.reference_height
    mats, _ = _matrices(skin, pose or {})
    xs, ys = [], []
    for p in skin.parts:
        surf = textures.get(p.texture)
        if surf is None:
            continue
        w, h = surf.get_width(), surf.get_height()
        ix0, iy0, ix1, iy1 = ink_box(surf)
        tw, th = w / skin.ppu, h / skin.ppu
        ox, oy = -p.anchor[0] * tw, -(1.0 - p.anchor[1]) * th
        for cx, cy in ((ix0, iy0), (ix1, iy0), (ix0, iy1), (ix1, iy1)):
            x, y = mats[p.id].transform_point(ox + cx / skin.ppu, oy + cy / skin.ppu)
            xs.append(x * k)
            ys.append(y * k)
    if not xs:
        return (-50 * k, -50 * k, 50 * k, 50 * k)
    return (min(xs), min(ys), max(xs), max(ys))


def rest_bounds(skin, textures, height_px):
    return pose_bounds(skin, textures, height_px)


def apply_gaze(skin, pose, dx, dy):
    """Nudge pupils toward (dx, dy), each in -1..1."""
    g = skin.gaze
    if not g:
        return
    m = g.get("maxOffset", 3)
    for i, pid in enumerate(g.get("pupils", [])):
        div = 1.0 + (g.get("divergence", 0) / 100.0) * (1 if i else -1)
        slot = pose.setdefault(pid, {})
        slot["x"] = slot.get("x", 0.0) + dx * m * div
        slot["y"] = slot.get("y", 0.0) + dy * m


# -- speech balloon ---------------------------------------------------------
BUBBLE_W = 300
_FONT = [None]


def ensure_font(family="Handjet"):
    """Name of the font to draw with — the bundled pixel one if fontconfig saw it.

    Registration happens in bootstrap_fontconfig() before Pango ever loads;
    by the time anything draws, it is either there or it is not.
    """
    if not _FONT[0]:
        _FONT[0] = family if _has_family(family) else "Sans"
    return _FONT[0]


def _has_family(name):
    fm = PangoCairo.FontMap.get_default()
    return any(f.get_name().lower() == name.lower() for f in fm.list_families())


def _layout(cr, text, size, max_w):
    lay = PangoCairo.create_layout(cr)
    lay.set_font_description(Pango.FontDescription(f"{_FONT[0] or 'Sans'} {size}"))
    lay.set_width(max_w * Pango.SCALE)
    lay.set_wrap(Pango.WrapMode.WORD_CHAR)
    lay.set_text(text, -1)
    return lay


def _balloon_path(cr, x, y, w, h, r, tail_x, tail_h):
    cr.new_path()
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
    cr.line_to(min(x + w - r, tail_x + tail_h * 0.7), y + h)
    cr.line_to(tail_x, y + h + tail_h)          # the little point at the cat
    cr.line_to(max(x + r, tail_x - tail_h * 0.7), y + h)
    cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    cr.close_path()


def draw_bubble(cr, text, tip_x, tip_y, max_w=BUBBLE_W, size=17, caret=False):
    """Balloon whose tail points down at (tip_x, tip_y). Returns its box in px."""
    if not text and not caret:
        return None
    pad, r, tail = 11, 9, 10
    lay = _layout(cr, text + ("▌" if caret else ""), size, max_w - 2 * pad)
    tw, th = lay.get_pixel_size()
    w, h = tw + 2 * pad, th + 2 * pad
    x, y = tip_x - w / 2, tip_y - tail - h

    cr.save()
    _balloon_path(cr, x, y, w, h, r, tip_x, tail)
    cr.set_source_rgba(0.07, 0.07, 0.10, 0.93)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.20)
    cr.set_line_width(2)
    cr.stroke()
    cr.set_source_rgba(0.96, 0.95, 0.92, 1.0)
    cr.move_to(x + pad, y + pad)
    PangoCairo.show_layout(cr, lay)
    cr.restore()
    return (x, y, x + w, y + h + tail)


# -- cat bed ----------------------------------------------------------------
# A boxy pet bed: raised headboard, bolsters wrapping the sides, a pale cushion
# and a front wall carrying a paw print. Built cell by cell on a coarse integer
# grid, because the cat beside it is pixel art on a 25-px grid and anything
# anti-aliased looks wrong next to it.
#
# It is painted in two passes: everything behind the cat, then the front wall
# over its paws, so the cat sits *in* the bed rather than on top of it.
GRID_W, GRID_H = 34, 22      # 1.55:1, as measured off the reference

# one warm ladder, darkest to lightest, sampled off the reference art
_P = [
    (0.29, 0.11, 0.02),   # 0 outline
    (0.44, 0.16, 0.03),   # 1 deep shadow
    (0.56, 0.22, 0.04),   # 2 shadow
    (0.68, 0.28, 0.07),   # 3 base
    (0.73, 0.31, 0.09),   # 4 back wall
    (0.79, 0.36, 0.12),   # 5 front wall
    (0.85, 0.41, 0.14),   # 6 bolster
    (0.88, 0.48, 0.22),   # 7 bolster lit
    (0.93, 0.57, 0.33),   # 8 cushion
    (0.96, 0.65, 0.42),   # 9 cushion lit
]


def _rrect(px, py, x0, y0, x1, y1, r):
    """Is the cell centre inside this rounded rectangle?"""
    if not (x0 <= px <= x1 and y0 <= py <= y1):
        return False
    cx = min(max(px, x0 + r), x1 - r)
    cy = min(max(py, y0 + r), y1 - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r + 0.2


def _ellipse(px, py, cx, cy, rx, ry):
    return ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2 <= 1.0


def _bed_cells():
    """Map every cell to a palette index, split into the two paint passes.

    The reference is a box with a raised back panel, a rim running round a wide
    oval cushion, and a front wall below it. The rim is shaded by height so it
    reads as rounded rather than as a flat frame.
    """
    back, front = {}, {}
    W, H = float(GRID_W), float(GRID_H)
    for gy in range(GRID_H):
        for gx in range(GRID_W):
            px, py = gx + 0.5, gy + 0.5
            body = _rrect(px, py, 0.0, 4.5, W, H - 0.5, 3.0)
            head = _rrect(px, py, W * 0.20, 0.0, W * 0.80, 12.0, 2.0)
            if not body and not head:
                continue
            # cushion: 14%..86% across, 31%..64% down, as measured
            # a rounded pad, not an oval — the reference has square-ish ends
            cushion = _rrect(px, py, W * 0.14, H * 0.30, W * 0.86, H * 0.62, 2.5)
            under = _rrect(px, py, W * 0.11, H * 0.27, W * 0.89, H * 0.70, 3.0)

            if cushion:
                back[(gx, gy)] = 9 if py < H * 0.45 else 8
            elif py >= H * 0.64:
                front[(gx, gy)] = 5 if py < H - 2.5 else 3       # front wall, skirt
            elif head and py < H * 0.30 and W * 0.22 < px < W * 0.78:
                back[(gx, gy)] = 6 if py < 1.5 else 4            # back panel + lit top
            else:
                if py < H * 0.20:
                    idx = 7                                      # rim catching the light
                elif py < H * 0.40:
                    idx = 6
                else:
                    idx = 5                                      # rim turning away
                if px < W * 0.11 or px > W * 0.89:
                    idx -= 1                                     # sides curve off
                if under and py > H * 0.45:
                    idx -= 1                                     # cushion casts down
                back[(gx, gy)] = max(3, idx)
    return back, front


_BACK, _FRONT = _bed_cells()
_ALL = {**_BACK, **_FRONT}

# a small paw stamped into the front wall, relative to its centre
_PAW = [(0, 2), (-1, 2), (1, 2), (0, 3), (-2, 0), (-1, 0), (1, 0), (2, 0), (0, -1)]
_PAW_AT = (GRID_W // 2, GRID_H - 4)
# three stitch marks on the cushion
_STITCH = [
    (cx + dx, 10 + dy)
    for cx in (GRID_W // 2 - 7, GRID_W // 2, GRID_W // 2 + 7)
    for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1))
]


def _paint(cr, unit, cells):
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    for (gx, gy), idx in cells.items():
        edge = any((gx + dx, gy + dy) not in _ALL
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        if not edge and (gx, gy) in _BACK and idx >= 8:
            # outline the cushion against the bolsters too, as the reference does
            edge = any(_ALL.get((gx + dx, gy + dy), 9) < 8
                       for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        if (gx, gy) in cells and (gx - _PAW_AT[0], gy - _PAW_AT[1]) in _PAW:
            idx = 2
            edge = False
        if (gx, gy) in _STITCH:
            idx = 6
            edge = False
        cr.set_source_rgb(*(_P[0] if edge else _P[idx]))
        cr.rectangle(gx * unit, gy * unit, unit, unit)
        cr.fill()
    cr.set_antialias(cairo.ANTIALIAS_DEFAULT)


# A curled sleeping cat covers 55% of the height asked for, reaching from
# 0.11 above its feet to 0.44 below (measured off the sleep clip). Those ratios
# place the cat in the basket by arithmetic instead of by eye.
SLEEP_TOP, SLEEP_BOTTOM = 0.11, 0.44
CAT_IN_BED = 1.00       # how tall the cat reads next to the bed
CAT_SUNK = 0.76         # how far down the bed its lowest point reaches


def bed_metrics(w, h):
    """Where the basket and its cat land inside a w x h box, without drawing.

    Returns (origin_x, origin_y, unit, cat_x, cat_feet_y, cat_height). The cat
    is taller than the basket, so callers that bake an image need cat_feet_y
    minus SLEEP_TOP * cat_height worth of headroom above the basket.
    """
    unit = min(w / GRID_W, h / GRID_H)
    ox, oy = (w - unit * GRID_W) / 2.0, (h - unit * GRID_H) / 2.0
    basket_h = unit * GRID_H
    cat_h = basket_h * CAT_IN_BED / (SLEEP_TOP + SLEEP_BOTTOM)
    feet = oy + basket_h * CAT_SUNK - cat_h * SLEEP_BOTTOM
    return ox, oy, unit, ox + unit * GRID_W / 2.0, feet, cat_h


def bed_back(cr, w, h):
    """Basket behind the cat. Returns (x, y, cat_height) for the cat itself."""
    ox, oy, unit, cat_x, feet, cat_h = bed_metrics(w, h)
    cr.save()
    cr.translate(ox, oy)
    _paint(cr, unit, _BACK)
    cr.restore()
    return cat_x, feet, cat_h


def bed_front(cr, w, h):
    """The near rim, painted over the cat's legs."""
    ox, oy, unit, _, _, _ = bed_metrics(w, h)
    cr.save()
    cr.translate(ox, oy)
    _paint(cr, unit, _FRONT)
    cr.restore()
