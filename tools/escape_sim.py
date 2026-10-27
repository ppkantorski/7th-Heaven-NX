#!/usr/bin/env python3
"""escape_sim.py -- render the Escape effect off-hardware, so claims are checked.

    python3 tools/escape_sim.py OUTDIR

Writes PNGs into OUTDIR. Re-runnable, and it is the ONLY thing allowed to
support a claim about what this effect looks like. Build 405 shipped a fix that
was verified numerically (the u bytes were right) and never rendered; the u
bytes being right says nothing about whether the TARGET holds what you think,
which is exactly what was wrong.

WHAT THE BINARY ACTUALLY DOES
=============================
`escape_setup` (+0x83B460) builds a stack object of TWO RECTS and a dword. The
common prologue fills in the defaults:

    ebp-0x14 A.x=0      -0x12 A.y=0    -0x10 A.w=0xA0   -0x0E A.h=0xA8
    ebp-0x0C B.x=0x140  -0x0A B.y=0    -0x08 B.w=0xA0   -0x06 B.h=0xA8
    ebp-0x04 = 2

and the battle arm (+0x83B5F4..) overrides A.x/A.y from the live battle rect,
B.x = rect.x + 0xA0, B.y = rect.y, and the dword to 1. It does NOT touch the
widths or heights, so in battle both captures are 160 x 168 units and B starts
160 units right of A.

Those rects go to the object at +0xDC/+0xE0/+0xE4/+0xE8, and the dword to
+0x10C/+0x110. The object's +0x114 (its slot index) is stored to 0x8FF0E8 for A
and to 0x8FF0EC for B -- the two handles the mesh binds.

THE MESH
========
`escape_uv_init` writes 840 POLY_FT4 records at 0xC22460, stride 0x80, in
idx = row*40 + col order over a 41 x 22 grid of points:

    x = col*8, y = row*8      ->  final = raw*2 + rect   (so 0..640, 0..336)
    u = col*8          col < 20, bound to handle A
    u = col*8 - 160    col >= 20, bound to handle B
    v = row*8

THE UNKNOWN THIS EXISTS TO PIN DOWN
===================================
What each capture target actually CONTAINS. The descriptor says 160x168 units,
but the unit is not established -- the same number can be texels, halfwords of
VRAM, or a downsampled destination. The models below enumerate the candidates;
render them and compare against the hardware screenshots. A model is adopted
only when it reproduces BOTH known observations:

    OBS-1  stock, 16:9   the whole screen appears TWICE, side by side,
                         squeezed into the central 4:3, black margins,
                         stopping at the UI line
    OBS-2  build 405     (bind A everywhere + u = col*4 + escapescale)
                         ONE image, stretched wide, zoomed in, still
                         stopping at the UI line

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:                                            # pragma: no cover
    sys.exit('need pillow:  pip install pillow --break-system-packages')

# ----------------------------------------------------------------- the frame

SCREEN_W, SCREEN_H = 854, 480          # the 16:9 frame in game units
FOUR_THREE_W = 640                     # the 4:3 region the mesh covers today
UI_TOP = 332                           # battle rect height: below this is UI

# The wide frame runs game x -107 .. 747; the 4:3 region the mesh draws into is
# game x 0..640, which sits CENTRED in it. Pixel column = game x + ORIGIN_X.
# Forgetting this makes a correct fix render as "shifted left and clipped".
ORIGIN_X = 107

GRID_COLS, GRID_ROWS = 41, 22          # grid POINTS
QUAD_COLS, QUAD_ROWS = 40, 21          # quads
STEP = 8                               # grid step in raw units
MESH_SCALE = 2                         # final = raw*2 + rect

TILE_W, TILE_H = 160, 168              # the descriptor's A.w / A.h
B_DX = 160                             # B.x - A.x


def source_frame(w=SCREEN_W, h=SCREEN_H):
    """A 16:9 frame with landmarks that survive any distortion, so that a
    doubling, a squeeze, a zoom or a crop is all readable at a glance."""
    im = Image.new('RGB', (w, h), (16, 16, 24))
    d = ImageDraw.Draw(im)
    # a coarse grid so stretching is measurable
    for x in range(0, w, 32):
        d.line([(x, 0), (x, h)], fill=(40, 40, 52))
    for y in range(0, h, 32):
        d.line([(0, y), (w, y)], fill=(40, 40, 52))
    # landmarks: red block LEFT, blue block RIGHT, yellow stripe CENTRE
    d.rectangle([40, 90, 190, 240], fill=(220, 60, 50))
    d.rectangle([w - 190, 90, w - 40, 240], fill=(60, 110, 230))
    d.rectangle([w // 2 - 14, 40, w // 2 + 14, h - 40], fill=(235, 210, 60))
    # edge markers, so a crop is obvious
    d.rectangle([0, 0, 18, h], fill=(0, 200, 120))
    d.rectangle([w - 19, 0, w - 1, h], fill=(200, 0, 190))
    # the UI band, which the effect is supposed to cover and currently does not
    d.rectangle([0, UI_TOP, w, h], fill=(70, 60, 90))
    d.text((24, UI_TOP + 16), 'UI BAND  (below the battle rect)',
           fill=(230, 230, 240))
    for i, x in enumerate(range(0, w, 100)):
        d.text((x + 4, 8), str(x), fill=(235, 235, 245))
    d.text((w // 2 - 40, h // 2 - 40), 'ESCAPE', fill=(255, 255, 255))
    return im


# ------------------------------------------------------- the capture models
#
# Each model answers: given a descriptor rect, what does that target hold, and
# how do texel coordinates (u, v) address it?

def capture(frame, rect, model):
    """Return a (tile_w, tile_h) image = what the target holds, in TEXELS.

    rect is (x, y, w, h) straight out of the descriptor.
    """
    x, y, w, h = rect
    if model == 'gpu':
        # ff7nx_escapegpu's Path B: the whole render target is fitted into the
        # effect's authored logical texture.  The rect does not crop it.
        return frame.resize((256, 256), Image.BILINEAR)
    if model == 'whole':
        # the port's observed defect: the narrowing is not honoured and the
        # target comes back holding the entire frame, resampled into w x h.
        return frame.resize((w, h), Image.BILINEAR)
    if model == 'units-are-texels':
        # the literal reading: the target holds exactly the w x h screen units
        # at (x, y), one texel per unit.
        return frame.crop((x, y, x + w, y + h))
    if model == 'units-are-halfwords':
        # 8bpp VRAM: one halfword holds two texels, so w units span 2w pixels
        # of screen and the tile is 2w texels wide.
        return frame.crop((x, y, x + 2 * w, y + h)).resize(
            (2 * w, h), Image.BILINEAR)
    if model in ('halves', 'halves-b-broken'):
        # THE DESCRIPTOR, TAKEN LITERALLY. Units are 2 screen px: w=160 -> 320
        # px, h=168 -> 336 px. A starts at rect.x, B at rect.x+160 (= +320 px),
        # so together they tile the 640-wide 4:3 region and stop at the UI line.
        # Texels = w x h, so the capture is a 2:1 downsample -- which is the
        # "low resolution" that was reported.
        sx = ORIGIN_X + x * 2
        return frame.crop((sx, y * 2, sx + w * 2, y * 2 + h * 2)).resize(
            (w, h), Image.BILINEAR)
    if model == 'downsampled':
        # the target is a w x h destination holding a 2w x 2h source region,
        # resampled down -- which would also explain "low resolution".
        return frame.crop((x, y, x + 2 * w, y + 2 * h)).resize(
            (w, h), Image.BILINEAR)
    raise ValueError(model)


MODELS = ('whole', 'units-are-texels', 'units-are-halfwords', 'downsampled')


# ------------------------------------------------------------- the renderer

def _repo():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if here not in sys.path:
        sys.path.insert(0, here)


def _escapescale():
    _repo()
    import ff7nx_escapescale
    return ff7nx_escapescale


def _escapetall():
    _repo()
    import ff7nx_escapetall
    return ff7nx_escapetall


def _escapeuv():
    _repo()
    import ff7nx_escapeuv
    return ff7nx_escapeuv


def _bilerp(tile, u, v):
    tw, th = tile.size
    u = min(max(u, 0), tw - 1)
    v = min(max(v, 0), th - 1)
    return tile.getpixel((int(u), int(v)))


def render(frame, cfg, out_w=SCREEN_W, out_h=SCREEN_H):
    """Draw the mesh. cfg carries every knob the fix can move."""
    model = cfg.get('model', 'whole')
    rect_x = cfg.get('rect_x', 0)
    rect_y = cfg.get('rect_y', 0)
    a_rect = cfg.get('a_rect', (rect_x, rect_y, TILE_W, TILE_H))
    b_rect = cfg.get('b_rect', (rect_x + B_DX, rect_y, TILE_W, TILE_H))
    bind_a_everywhere = cfg.get('bind_a_everywhere', False)
    u_of = cfg.get('u_of')                 # (col) -> (u_left, u_right)
    v_of = cfg.get('v_of')                 # (row) -> (v_top, v_bottom)
    widen = cfg.get('widen', False)        # the escapescale transform
    rows = cfg.get('rows', QUAD_ROWS)
    row_step = cfg.get('row_step', STEP)

    if u_of is None:
        def u_of(col):
            if col < 20:
                return col * 8, col * 8 + 8
            return col * 8 - 160, col * 8 - 152
    if v_of is None:
        def v_of(row):
            return row * STEP, row * STEP + STEP

    tile_a = capture(frame, a_rect, model)
    if model == 'halves-b-broken':
        # the hypothesis that explains the STOCK doubling: capture B's handle
        # resolves to the same slot as A, so the mesh's right half draws A too.
        tile_b = tile_a
    else:
        tile_b = capture(frame, b_rect, model)

    def gx(col):
        x = col * STEP * MESH_SCALE + rect_x
        if widen:
            # the REAL transform, imported from the shipped module -- so this
            # picture is a check on the code rather than on a copy of it
            x = _escapescale().expected(x)
        return x + ORIGIN_X

    heighten = cfg.get('heighten', False)

    def gy(row):
        # the mesh is 21 rows x 8 x 2 = 336 units tall, which is the battle
        # rect height -- so the effect stops at the UI line. The capture holds
        # the WHOLE 480-tall frame squeezed into 168 texels, so stretching the
        # mesh to 480 makes the picture 1:1 vertically and covers the UI band.
        y = row * row_step * MESH_SCALE + rect_y
        if heighten:
            y = _escapetall().expected(y)
        return y

    out = Image.new('RGB', (out_w, out_h), (0, 0, 0))
    px = out.load()
    for row in range(rows):
        y0, y1 = gy(row), gy(row + 1)
        v0, v1 = v_of(row)
        for col in range(QUAD_COLS):
            x0, x1 = gx(col), gx(col + 1)
            tile = tile_a if (bind_a_everywhere or col < 20) else tile_b
            u0, u1 = u_of(col)
            if x1 <= x0 or y1 <= y0:
                continue
            for yy in range(max(0, y0), min(out_h, y1)):
                fy = (yy - y0) / float(y1 - y0)
                v = v0 + fy * (v1 - v0)
                for xx in range(max(0, x0), min(out_w, x1)):
                    fx = (xx - x0) / float(x1 - x0)
                    px[xx, yy] = _bilerp(tile, u0 + fx * (u1 - u0), v)
    return out


# ------------------------------------------------------------------ figures

def label(im, text, h=30):
    out = Image.new('RGB', (im.width, im.height + h), (0, 0, 0))
    out.paste(im, (0, h))
    ImageDraw.Draw(out).text((8, 9), text, fill=(255, 255, 255))
    return out


def stack(images, gap=10):
    w = max(i.width for i in images)
    h = sum(i.height for i in images) + gap * (len(images) - 1)
    out = Image.new('RGB', (w, h), (0, 0, 0))
    y = 0
    for i in images:
        out.paste(i, (0, y))
        y += i.height + gap
    return out


def u_col4(col):
    """The shipped UV model, imported rather than restated."""
    return _escapeuv().expected_u(col)


def v_full(row):
    """The shipped full-frame V model, coupled to the height transform."""
    return _escapeuv().expected_v(row)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    outdir = argv[0] if argv else '.'
    os.makedirs(outdir, exist_ok=True)
    frame = source_frame()
    frame.save(os.path.join(outdir, 'escape_sim_source.png'))

    # --- which capture model reproduces the two hardware observations? ---
    panels = [label(frame, 'SOURCE  (what the screen looks like with no effect)')]
    for model in MODELS:
        stock = render(frame, {'model': model})
        panels.append(label(stock, 'OBS-1 candidate  stock UVs + stock bind'
                                   '   model=%s' % model))
    stack(panels).save(os.path.join(outdir, 'escape_sim_models_stock.png'))

    panels = [label(frame, 'SOURCE')]
    for model in MODELS:
        shipped = render(frame, {'model': model, 'bind_a_everywhere': True,
                                 'u_of': u_col4, 'widen': True})
        panels.append(label(shipped, 'OBS-2 candidate  build 405 '
                                     '(bind A + u=col*4 + widen)'
                                     '   model=%s' % model))
    stack(panels).save(os.path.join(outdir, 'escape_sim_models_405.png'))

    fixed = render(frame, {'model': 'gpu', 'bind_a_everywhere': True,
                           'u_of': u_col4, 'v_of': v_full,
                           'widen': True, 'heighten': True})
    label(fixed, 'FIX  GPU full-frame capture + full U/V + centered geometry') \
        .save(os.path.join(outdir, 'escape_sim_fixed.png'))
    print('wrote escape_sim_source.png, escape_sim_models_stock.png, '
          'escape_sim_models_405.png and escape_sim_fixed.png to %s' % outdir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
