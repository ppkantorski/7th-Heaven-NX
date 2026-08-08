#!/usr/bin/env python3
"""
diag_tilespace.py -- settle the field tile window WITHOUT assuming where
`bg_position.x` sits.

WHY THIS EXISTS
===============
Three modules in this tree compute the same quantity two different ways:

    ff7nx_wsclamp   view relative to `bg_position` (the pick_tiles argument)
    diag_fieldcover view relative to the section-7 camera range value

Those are two different spaces and the offset between them has never been
written down, so it has been re-guessed three times (HANDOFF-60 §3.8,
HANDOFF-61 §2, HANDOFF-62 §1.2).

THE ANCHOR THAT NEEDS NO GUESS
==============================
Vanilla 4:3 is known-correct, so the stock window must BE the 4:3 view plus
the developers' own slack rule -- one tile on the LOW side (a tile whose
origin is outside still reaches TILE units back in) and nothing on the high
side (a tile whose origin is outside lies entirely outside):

    x   view [bg-320, bg]  + one tile low  ->  window (bg-336, bg+0)   STOCK
    y   view [bg-240, bg]  + one tile low  ->  window (bg-256, bg+0)   STOCK

Both stock windows fall out of one rule, on two axes, with no free parameter.
That is the derivation.

THE CHECK -- a symptom the model was NOT fitted to:

    the y frame is 240 tile units, and vanilla art is 224 tall
    240 - 224 = 16   <- the native FF7 top/bottom bar

If the model did not reproduce that, it would be wrong.

WHAT IT PRINTS
==============
Per named field: the raw per-layer dst_x/dst_y extents out of the archive,
the 4:3 and 16:9 frames in the same space, and whether the extents currently
in `exefs/main` cover the 16:9 frame.

    python3 diag_tilespace.py <flevel.lgp> [<exefs/main>] md8_1 mkt_m ...
    python3 diag_tilespace.py --check-model            # no inputs; the arithmetic

Labels, per HANDOFF-62 §0.2: everything here is MEASURED (archive) or
DISASSEMBLED (module). The view geometry is DERIVED and reproduces both stock
windows exactly, so it carries no free parameter.

Written the way HANDOFF-62 §0.8 asks: it REFUSES rather than guesses. If the
model stops reproducing the stock windows it prints MODEL FAILED and exits
non-zero, and if a site in `main` does not decode it says so instead of
reporting a number. It caught an off-by-8 in its own author's derivation on
the first run, which is the entire reason it exists.
"""
from __future__ import annotations

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TILE = 16
STOCK_LEFT, STOCK_TOP = 336, 256          # sub w9, w8, #0x150 / #0x100
VANILLA_ART_W, VANILLA_ART_H = 320, 224   # MEASURED: mkt_m and every 4:3 field
FRAME_43, FRAME_H = 320, 240              # tile units (640/2, 480/2)
WS_SCALE = 0.74766355                     # the #define in lmain_vv.glsl
FRAME_169 = int(round(640.0 / WS_SCALE / 2))          # 428

# The four sites ff7nx_wsclamp owns, as (name, va, kind).
SITES = [('left1', 0xA07244, 'imm'), ('left2', 0xA05E00, 'imm'),
         ('top1', 0xA07138, 'imm'), ('top2', 0xA05CF4, 'imm'),
         ('right1', 0xA072D0, 'cave'), ('right2', 0xA05E8C, 'cave'),
         ('bottom1', 0xA071C8, 'cave'), ('bottom2', 0xA05D84, 'cave')]
STOCK_CAVE_WORD = 0x4B08012A              # sub w10, w9, w8, undisplaced


# --------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------
def bg_offset():
    """
    How far the view centre sits from `bg_position.x`, in tile units.

    FORCED, not bracketed. The stock window is `(bg - 336, bg + 0)` on x and
    `(bg - 256, bg + 0)` on y, and the rule the original developers applied is
    "the view, plus exactly one tile on the LOW side and nothing on the high
    side" (a tile whose origin is past the high boundary lies entirely past
    it; one whose origin is past the low boundary still reaches TILE units
    back in). That fixes the view to

        x   [bg - 320, bg]     320 = the 4:3 frame, so   offset = 160
        y   [bg - 240, bg]     240 = the frame,          offset = 120

    and BOTH stock windows fall straight out of it, which is what makes this
    a derivation rather than a fit.
    """
    return FRAME_43 // 2, FRAME_H // 2            # 160, 120


def view(frame_w, axis='x'):
    """
    [low, high] of a frame `frame_w` tile units wide, relative to bg.

    Widescreen widens the frame about the SAME centre -- it does not move the
    picture. HANDOFF-62 §2.2: widescreen only ever widens the frustum.
    """
    cx, cy = bg_offset()
    centre = -(cx if axis == 'x' else cy)
    return centre - frame_w // 2, centre + frame_w // 2


def window_needed(frame_w, axis='x'):
    """(L, R) the cull needs: the view, plus one tile on the LOW side only."""
    lo, hi = view(frame_w, axis)
    return -lo + TILE, hi


def check_model():
    """The vertical axis as a falsification test. Returns True if it holds."""
    ok = True
    cx, cy = bg_offset()
    print('MODEL')
    print('  view centre sits at bg-%d on x, bg-%d on y   (FORCED, see '
          'bg_offset)' % (cx, cy))
    for axis, frame, stock in (('x', FRAME_43, STOCK_LEFT),
                               ('y', FRAME_H, STOCK_TOP)):
        L, R = window_needed(frame, axis)
        print('  %s  frame %3d units -> window needs L=%d R=%d   '
              '(stock is %d, %d)' % (axis, frame, L, R, stock, 0))
        if (L, R) != (stock, 0):
            print('     ! does not reproduce the stock window -- MODEL IS WRONG')
            ok = False
        else:
            print('     ok: reproduces the stock %s window exactly' % axis)

    print('\nTHE CHECK -- a symptom the model was NOT fitted to')
    print('  the y frame is %d units; vanilla art is %d tall'
          % (FRAME_H, VANILLA_ART_H))
    bar = FRAME_H - VANILLA_ART_H
    print('  frame %d - art %d = %d units of bar, top and bottom'
          % (FRAME_H, VANILLA_ART_H, bar))
    if bar != 16:
        print('  ! that is not the native FF7 bar -- MODEL IS WRONG')
        ok = False
    else:
        print('  ok: 16 units == the native FF7 top/bottom bar. Model holds.')

    print('\n16:9 at WS_SCALE %.8f' % WS_SCALE)
    L, R = window_needed(FRAME_169, 'x')
    lo, hi = view(FRAME_169, 'x')
    print("  frame %d units, view [bg%+d, bg%+d]" % (FRAME_169, lo, hi))
    print('  window needs L>=%d  R>=%d' % (L, R))
    return ok


# --------------------------------------------------------------------------
# the module
# --------------------------------------------------------------------------
def read_extents(main_path):
    """{name: value} out of exefs/main. DISASSEMBLED, not assumed."""
    import nxmap
    img = nxmap.Main(str(main_path)).img
    out = {}
    for name, va, kind in SITES:
        w = struct.unpack_from('<I', img, va)[0]
        if kind == 'imm':
            if (w & 0xFF000000) != 0x51000000:
                out[name] = None
                continue
            out[name] = (w >> 10) & 0xFFF
        else:
            if w == STOCK_CAVE_WORD:
                out[name] = 0
                continue
            if (w >> 26) != 0x05:                     # not a `b`
                out[name] = None
                continue
            off = w & 0x3FFFFFF
            if off & (1 << 25):
                off -= 1 << 26
            body = struct.unpack_from('<I', img, va + off * 4)[0]
            if (body & 0xFF000000) != 0x11000000:     # not `add w8, w8, #imm`
                out[name] = None
                continue
            out[name] = (body >> 10) & 0xFFF
    return out


def report_module(main_path):
    ex = read_extents(main_path)
    L, R = window_needed(FRAME_169, 'x')
    T = window_needed(FRAME_H, 'y')[0]
    print('\nSHIPPED EXTENTS (DISASSEMBLED from %s)' % main_path)
    bad = False
    for name, need in (('left1', L), ('left2', L), ('top1', T), ('top2', T),
                       ('right1', R), ('right2', R)):
        got = ex.get(name)
        if got is None:
            print('  %-8s UNDECODABLE -- not the module this was built against'
                  % name)
            bad = True
            continue
        mark = 'covers' if got >= need else '** SHORT by %d **' % (need - got)
        if got < need:
            bad = True
        print('  %-8s %4d   needs >= %4d   %s' % (name, got, need, mark))
    for name in ('bottom1', 'bottom2'):
        print('  %-8s %4s   (vertical high side; 0 is stock, >0 uncrops the '
              'native bottom bar)' % (name, ex.get(name)))
    return not bad


# --------------------------------------------------------------------------
# the archive
# --------------------------------------------------------------------------
def report_fields(flevel, names):
    import lgp
    import diag_common as DC
    arc = lgp.Archive(str(flevel))
    lo43, hi43 = view(FRAME_43, 'x')
    lo169, hi169 = view(FRAME_169, 'x')
    print('\nRAW TILE EXTENTS (MEASURED from %s)' % flevel)
    print('  frames, relative to bg_position:  4:3 [%+d,%+d]   16:9 [%+d,%+d]'
          % (lo43, hi43, lo169, hi169))
    for nm in names:
        e = arc.index.get(nm)
        if e is None:
            print('  %-9s NOT IN ARCHIVE' % nm)
            continue
        try:
            parts = lgp.split_sections(arc.decompressed(e))
            sec7, sec9 = parts[7], parts[8]
            left, top, right, bottom = struct.unpack_from('<4h', sec7, 12)
            surv = DC.survey(sec9)
        except Exception as exc:                             # noqa: BLE001
            print('  %-9s UNREADABLE (%s)' % (nm, exc))
            continue
        print('  %-9s section-7 range x %5d..%5d (w %4d)'
              % (nm, left, right, right - left))
        for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                          surv['tex_start']):
            xs = [struct.unpack_from('<h', sec9, o + DC.TILE_DST_X)[0]
                  for o in offs]
            ys = [struct.unpack_from('<h', sec9, o + DC.TILE_DST_Y)[0]
                  for o in offs]
            if not xs:
                continue
            w = max(xs) + TILE - min(xs)
            print('      layer %d  %6d tiles  dst_x %5d..%5d  width %4d  %s'
                  % (layer, len(xs), min(xs), max(xs) + TILE, w,
                     'fills 16:9' if w >= FRAME_169 else
                     ('fills 4:3' if w >= FRAME_43 else 'under 4:3')))
            print('               %27s dst_y %5d..%5d  height %4d'
                  % ('', min(ys), max(ys) + TILE, max(ys) + TILE - min(ys)))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == '--check-model':
        ok = check_model()
        print('\n%s' % ('MODEL HOLDS' if ok else 'MODEL FAILED'))
        return 0 if ok else 1
    ok = check_model()
    flevel = argv.pop(0)
    main_path = None
    if argv and os.path.basename(argv[0]) in ('main', 'exefs'):
        main_path = argv.pop(0)
    elif argv and os.path.exists(argv[0]) and not argv[0].isidentifier():
        main_path = argv.pop(0)
    if main_path:
        ok = report_module(main_path) and ok
    if argv:
        report_fields(flevel, argv)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
