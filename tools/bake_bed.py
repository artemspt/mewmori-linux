"""Render the panel applet's two sprites from the same cairo bed the cat uses.

    python3 tools/bake_bed.py

Run this after changing draw_bed() or the default skin.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mewmori import render                      # noqa: E402
from mewmori.rig import Animator, Library, Skin  # noqa: E402

import cairo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# into the repository copy, not the installed one: plasmoid/install.sh is what
# moves it to the panel, so a bake never silently diverges from what is shipped
OUT = ROOT / "plasmoid/com.mewmori.bed/contents/images"
# The grid is GRID_W x GRID_H cells; baking at a whole number of pixels per
# cell keeps the art crisp, and 3 px/cell lands within a pixel of the usual
# 44-52 px panel height, so the applet displays it near 1:1.
CELL = 3
BW, BH = render.GRID_W * CELL, render.GRID_H * CELL

# the cat stands taller than the basket, so the canvas grows upwards by exactly
# as much as it overhangs — worked out, not guessed
_, _, _, _, _feet, _cat_h = render.bed_metrics(BW, BH)
HEAD = max(0, int(round(render.SLEEP_TOP * _cat_h - _feet)))
CANVAS_H = BH + HEAD

skin = Skin.load(ROOT / "assets/skins/classic_cat")
lib = Library(ROOT / "assets/animation")
tex = render.load_textures(skin)
OUT.mkdir(parents=True, exist_ok=True)

for name, occupied in (("bed_empty", False), ("bed_sleeping", True)):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, BW, CANVAS_H)
    cr = cairo.Context(surf)
    cr.translate(0, HEAD)
    px, py, cat_h = render.bed_back(cr, BW, BH)
    if occupied:
        anim = Animator(lib, "sleep")
        for _ in range(90):
            pose = anim.update(1 / 60)
        cr.save()
        cr.translate(px, py)
        render.draw(cr, skin, tex, pose, cat_h)
        cr.restore()
    render.bed_front(cr, BW, BH)
    surf.write_to_png(str(OUT / f"{name}.png"))
    print(f"  {name}.png  {BW}x{CANVAS_H}  (корзина {BW}x{BH}, запас сверху {HEAD})")
