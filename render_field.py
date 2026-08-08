#!/usr/bin/env python3
"""
render_field.py -- draw a field's background out of a BUILT flevel.lgp, the
way the console will draw it, and write a PNG.

    python3 render_field.py <flevel.lgp> mds6_1 mds6_2 [-o out.png]
    python3 render_field.py <flevel.lgp> mds6_1 --layers 1
    python3 render_field.py <a.lgp> --against <b.lgp> mds6_1     # A|B|diff

WHY THIS EXISTS
===============
HANDOFF-78 6.5: "every hardware test costs the user 20+ minutes". Every colour
question in this project has been answered by building, copying to an SD card,
booting, and looking -- and three of the resulting diagnoses were wrong because
a screenshot cannot tell you WHICH pass put a pixel there.

`ff7nx_marginblack.render_margin` is a GEOMETRY check with documented blind
spots (HANDOFF-78 3.4): it treats index 0 as transparent and skips
`pid >= npg`, so it cannot see either of the two colour bugs this project
actually has. This renders COLOUR, through the palette each tile names, with
index 0 drawn -- which is what FFNx `ff7/field/field.cpp:56` establishes the
engine does for depth-1 pages (no `color_key` is set for `type == 1`).

WHAT IT IS NOT
==============
Not an emulator. It composites layer 1 (and optionally 2) in tile order with
no blend modes, no fx animation frame selection, no camera. It answers "what
colour is the art in this archive", not "what does the frame look like".
Additive layers are drawn ADDITIVELY so the steam-style defects are visible.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import field_bg_native as FN
import ff7nx_marginblack as MB
import lgp

TILE = 16
UV_SCALE = 10_000_000
T_DSTX, T_DSTY = 2, 4
T_PAL = 22
T_TEX, T_TEX2 = 32, 34
T_SRC_X_BIG, T_SRC_Y_BIG = 42, 46


def _pal_rgb(sec3):
    """Section-3 palettes as (npg, cpp, 3) uint8 RGB. Index 0 included."""
    cols, hdr, npg, cpp = MB.palette_colours(sec3)
    v = cols.astype(np.uint32).reshape(npg, cpp)
    r = ((v & 31) << 3).astype(np.uint8)
    g = (((v >> 5) & 31) << 3).astype(np.uint8)
    b = (((v >> 10) & 31) << 3).astype(np.uint8)
    return np.stack([r, g, b], -1)


def _d2_rgb(buf):
    v = buf.astype(np.uint32)
    return np.stack([(((v >> 11) & 31) << 3).astype(np.uint8),
                     (((v >> 5) & 63) << 2).astype(np.uint8),
                     ((v & 31) << 3).astype(np.uint8)], -1)


def render(raw, layers=(1, 2), px=256):
    """(H, W, 3) uint8, and the (x0, y0) of the canvas in game units."""
    parts = lgp.split_sections(raw)
    sec9 = parts[8]
    pages, tex_start, _ = FN.parse_texture_block(sec9, px)
    pmap = {p.slot: p for p in pages if p is not None}
    pal = _pal_rgb(parts[3])
    npg = pal.shape[0]

    arrays = {}
    for s, p in pmap.items():
        if p.depth == 1:
            arrays[s] = np.frombuffer(p.data, np.uint8).reshape(256, 256)
        else:
            arrays[s] = _d2_rgb(np.frombuffer(p.data, '<u2')
                                .reshape(p.px, p.px))

    todo = []
    for layer, offs in __import__('diag_common').walk_layers(
            sec9, sec9.find(b'BACK'), tex_start):
        if layer not in layers:
            continue
        for o in offs:
            todo.append((layer, o))
    if not todo:
        raise ValueError('no tiles')

    xs = [struct.unpack_from('<h', sec9, o + T_DSTX)[0] for _, o in todo]
    ys = [struct.unpack_from('<h', sec9, o + T_DSTY)[0] for _, o in todo]
    x0, y0 = min(xs), min(ys)
    W, H = max(xs) - x0 + TILE, max(ys) - y0 + TILE
    canvas = np.zeros((H, W, 3), np.int32)

    for (layer, o), tx, ty in zip(todo, xs, ys):
        slot = sec9[o + T_TEX]
        fx = sec9[o + T_TEX2]
        eff = fx if (fx and fx in pmap) else slot
        p = pmap.get(eff)
        if p is None:
            continue
        grid = 8 if p.size_flag else 16
        u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
        cx = int(round(u / UV_SCALE * grid))
        cy = int(round(v / UV_SCALE * grid))
        step = 256 // grid if p.depth == 1 else p.px // grid
        sx, sy = cx * step, cy * step
        a = arrays[eff]
        if p.depth == 1:
            blk = a[sy:sy + step, sx:sx + step]
            if blk.shape[:2] != (step, step):
                continue
            pi = sec9[o + T_PAL]
            if pi >= npg:
                pi = npg - 1                    # HANDOFF-78 3.4: CLAMP
            rgbblk = pal[pi][blk]
        else:
            rgbblk = a[sy:sy + step, sx:sx + step]
            if rgbblk.shape[:2] != (step, step):
                continue
        if step != TILE:
            k = step // TILE
            rgbblk = rgbblk[::k, ::k][:TILE, :TILE]
        dy, dx = ty - y0, tx - x0
        # LAYER 1 OVERWRITES. EVERY OVERLAY ADDS.
        #
        # Layer 1 is the backdrop and has nothing behind it. Layers 2-4 are
        # overlays, and the mode they are drawn in comes from the tile, not
        # from the page -- FFNx calls `common_blendmode()` per draw. This
        # renderer does not read the tile's mode, so it models every overlay as
        # ADDITIVE, which is the mode that makes palette entry 0 load-bearing:
        # black adds nothing, anything else lays a wash over the layer-1 art.
        #
        # That approximation is the point. It reproduces the grey rectangles
        # reported from hardware in mds6_22 and mds6_1, so the defect can be
        # seen and fixed here instead of costing a 20-minute build. It is NOT
        # a claim about what the frame looks like -- an overlay that is really
        # drawn in average or opaque mode will be too bright here.
        if layer != 1 or (0x0F <= eff < 0x1A and p.depth == 1) \
                or (0x21 <= eff < 0x2A and p.depth == 2):
            canvas[dy:dy + TILE, dx:dx + TILE] += rgbblk
        else:
            canvas[dy:dy + TILE, dx:dx + TILE] = rgbblk
    return np.clip(canvas, 0, 255).astype(np.uint8), (x0, y0)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('fields', nargs='+')
    ap.add_argument('--against', help='second archive; renders A | B | diff')
    ap.add_argument('--layers', default='1,2')
    ap.add_argument('--px', type=int, default=256)
    ap.add_argument('-o', '--out', default='field_render.png')
    a = ap.parse_args(argv)
    layers = tuple(int(x) for x in a.layers.split(','))

    from PIL import Image, ImageDraw
    A = lgp.Archive(a.flevel)
    B = lgp.Archive(a.against) if a.against else None
    rows = []
    for nm in a.fields:
        e = A.index.get(nm)
        if e is None or not A.is_field(e):
            print(f'  ! {nm}: not a field', file=sys.stderr)
            continue
        try:
            ia, _ = render(A.decompressed(e), layers, a.px)
        except Exception as exc:                               # noqa: BLE001
            print(f'  ! {nm}: {type(exc).__name__} {exc}', file=sys.stderr)
            continue
        cells = [(nm, ia)]
        if B is not None and nm in B.index:
            ib, _ = render(B.decompressed(B.index[nm]), layers, a.px)
            if ib.shape == ia.shape:
                d = np.abs(ia.astype(np.int32) - ib.astype(np.int32))
                cells.append((f'{nm} (B)', ib))
                cells.append((f'diff  mean {d.mean():.1f}  max {d.max()}',
                              np.clip(d * 4, 0, 255).astype(np.uint8)))
            else:
                cells.append((f'{nm} (B, size differs)', ib))
        rows.append(cells)

    if not rows:
        print('nothing rendered', file=sys.stderr)
        return 1
    PAD, LBL = 8, 18
    w = max(sum(c[1].shape[1] for c in r) + PAD * (len(r) + 1) for r in rows)
    h = sum(max(c[1].shape[0] for c in r) + LBL + PAD for r in rows) + PAD
    img = Image.new('RGB', (w, h), (20, 20, 24))
    d = ImageDraw.Draw(img)
    y = PAD
    for r in rows:
        x = PAD
        for label, im in r:
            d.text((x, y), label, fill=(215, 215, 220))
            img.paste(Image.fromarray(im), (x, y + LBL))
            x += im.shape[1] + PAD
        y += max(c[1].shape[0] for c in r) + LBL + PAD
    img.save(a.out)
    print(f'  wrote {a.out}  {img.size}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
