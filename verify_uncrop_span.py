#!/usr/bin/env python3
"""
verify_uncrop_span.py -- does the layer-1/2 tile cull remove any tile that
the field black bars would have shown?

    python3 verify_uncrop_span.py <flevel.lgp> [--main exefs/main] [field ...]

WHY THIS EXISTS
===============
HANDOFF-83 4.6 observed that `top1`/`top2` are `stock` in the shipped module
while the top of the field is not drawn, and made that the live lead: "the
tiles were being thrown away before the renderer ever saw them."

Both halves of that sentence are true and the inference between them is not.
`check_all` reports what the CODE says; it cannot report whether the window
that code defines is BINDING on the data. This script answers the second
question, which is the only one that matters, and it answers it offline.

THE ARITHMETIC, FROM FFNx `ff7/field/background.cpp:66`
======================================================
    initial_pos.y = mult * (224 - bg.y)                  ORIGIN_Y = 224
    dst_y         = initial_pos.y + mult * tile.y
                  = (tile.y + 224 - bg.y) * mult

and the culls decompiled by `ff7nx_wsclamp` from the x86 at 0x640C49:

    if (tile.y <= bg.y - TOP)    continue;      TOP    stock 256
    if (tile.y >= bg.y + BOTTOM) continue;      BOTTOM stock 0, shipped 16

so the ADMITTED window is the half-open interval

    tile.y in ( bg.y - TOP , bg.y + BOTTOM )        width TOP + BOTTOM

which is a window in TILE SPACE that slides with the camera. `bg.y` is
therefore the only unknown, and this script does not guess it: it reports the
range of `bg.y` over which NOTHING is culled, and compares that with the range
`bg.y` must lie in for the art to be on screen at all. If the first contains
the second, the cull cannot be the mechanism -- for any camera position,
without knowing which one the field actually uses.

WHAT `--uncrop` CHANGES
=======================
FFNx's uncrop is `y 16/448 -> 0/480` at `renderer.cpp:1668`, i.e. the view
grows from 224 to 240 GAME units, 8 at each end. `--uncrop` widens the
required window by that 8 so the same question can be asked of the state we
are trying to reach, not just the one we are in.
"""
from __future__ import annotations

import argparse
import collections
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC                                        # noqa: E402
import field_bg_native as FN                                    # noqa: E402
import lgp                                                      # noqa: E402

T_DSTY = 4
TILE = 16
ORIGIN_Y = 224
MULT = 2

VIEW_43 = 224            # game units the 4:3 field view shows, of 240 drawn
VIEW_UNCROP = 240        # what enable_uncrop opens it to


def tile_y_extent(raw):
    """(lo, hi) game-y covered by layers 1 and 2, hi exclusive."""
    parts = lgp.split_sections(raw)
    sec9 = parts[8]
    _pages, tex_start, _ = FN.parse_texture_block(sec9, 256)
    ys = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        if layer not in (1, 2):
            continue
        ys += [struct.unpack_from('<h', sec9, o + T_DSTY)[0] for o in offs]
    if not ys:
        return None
    return min(ys), max(ys) + TILE


def window_dst(top, bottom):
    """
    The dst-y span the cull ADMITS, in the frame's own coordinates.

    THIS IS THE TEST THAT MATTERS AND IT DOES NOT INVOLVE bg.y.

    An earlier version of this script asked "does the cull remove any tile
    on this field", which reports `binds` on all 490 scrolling fields and
    means nothing: removing off-screen art is the cull's JOB. The question
    is whether the window it admits CONTAINS the view, and because both are
    expressed relative to bg.y, bg.y cancels:

        admitted tile.y in (bg.y - top, bg.y + bottom)
        dst_y            = (tile.y + 224 - bg.y) * MULT
        -> admitted dst   ((224 - top) * MULT, (224 + bottom) * MULT)

    Tiles are TILE units tall and sit on a TILE grid, so the last admitted
    row covers a further TILE * MULT past its origin -- which is why the
    high side needs no tile term and the low side already has 64 units of
    slack at stock.
    """
    return ((ORIGIN_Y - top) * MULT, (ORIGIN_Y + bottom) * MULT)


def view_dst(view):
    """The dst-y span the field SHOWS. [16,464] at 4:3, [0,480] uncropped."""
    pad = (240 - view) // 2
    return (pad * MULT, (pad + view) * MULT)


def onscreen_bg_y(lo, hi, view):
    """
    Interval of bg.y for which the art still covers the whole view.

    The field draws a 240-game-unit tall picture and SHOWS `view` of it,
    centred -- which is FFNx's `y == 16 && height == 448` scissor stated in
    game units rather than doubled ones, and `enable_uncrop` opening it to
    `0 / 480` is the same statement with `view = 240`.

        pad       = (240 - view) / 2               8 at 4:3, 0 uncropped
        view spans dst [2*pad, 2*(pad + view)]     [16,464] and [0,480]
        dst_y     = (tile.y + 224 - bg.y) * 2

    Art reaches the top edge when  dst(lo) <= 2*pad,
    and the bottom edge when       dst(hi) >= 2*(pad + view).
    """
    pad = (240 - view) // 2
    return (lo + ORIGIN_Y - pad, hi + ORIGIN_Y - pad - view)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('fields', nargs='*')
    ap.add_argument('--main', help='exefs/main; read the real cull values')
    ap.add_argument('--top', type=int, default=256)
    ap.add_argument('--bottom', type=int, default=16)
    ap.add_argument('--uncrop', action='store_true')
    a = ap.parse_args(argv)

    top, bottom = a.top, a.bottom
    if a.main:
        import nxmap
        import ff7nx_wsclamp as W
        img = nxmap.Main(a.main).img
        top = W.read_value(img, 'top1')
        bottom = W.read_value(img, 'bottom1')
        print('cull values READ FROM THE MODULE: top=%s bottom=%s' %
              (top, bottom))
    view = VIEW_UNCROP if a.uncrop else VIEW_43
    print('view %d game units (%s)\n' % (view, 'UNCROPPED' if a.uncrop
                                         else '4:3'))

    # ---- part 1: the cull window against the view. bg.y cancels. ---------
    w_lo, w_hi = window_dst(top, bottom)
    v_lo, v_hi = view_dst(view)
    covers = w_lo <= v_lo and v_hi <= w_hi
    print('CULL WINDOW   dst y %5d .. %-5d   (top=%d bottom=%d)'
          % (w_lo, w_hi, top, bottom))
    print('VIEW          dst y %5d .. %-5d' % (v_lo, v_hi))
    print('  -> %s   slack %d above, %d below\n'
          % ('WINDOW CONTAINS THE VIEW -- the cull cannot be the mechanism'
             if covers else '*** THE CULL CLIPS THE VIEW ***',
             v_lo - w_lo, w_hi - v_hi))

    # ---- part 2: does the ART reach, once the view is opened? ------------
    A = lgp.Archive(a.flevel)
    names = a.fields or [n for n, e in A.index.items() if A.is_field(e)]
    short = []
    n = 0
    hist = collections.Counter()
    for nm in names:
        e = A.index.get(nm)
        if e is None or not A.is_field(e):
            continue
        try:
            ext = tile_y_extent(A.decompressed(e))
        except Exception:                                       # noqa: BLE001
            continue
        if ext is None:
            continue
        lo, hi = ext
        n += 1
        hist[hi - lo] += 1
        if hi - lo < view:
            short.append((nm, lo, hi, hi - lo))
        if a.fields:
            fits = onscreen_bg_y(lo, hi, view)
            print('%-10s art y %5d..%-5d (%d units)  %s'
                  % (nm, lo, hi, hi - lo,
                     'covers the view for bg.y in [%d, %d]' % fits
                     if hi - lo >= view else
                     '*** ONLY %d UNITS -- cannot fill a %d-unit view'
                     % (hi - lo, view)))

    print('\nfields checked: %d' % n)
    print('art shorter than the %d-unit view: %d field(s)' % (view, len(short)))
    for row in sorted(short, key=lambda t: t[3])[:20]:
        print('   %-10s y %5d..%-5d  %d units' % row)
    if short:
        print('   (these keep a bar after uncrop. That is an ART limit, not '
              'a code one -- do not read it as the patch failing.)')
    return 0 if covers else 1


if __name__ == '__main__':
    raise SystemExit(main())
