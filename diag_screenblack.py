#!/usr/bin/env python3
"""
diag_screenblack.py -- the cells that are black ON HARDWARE, not in the file.

The offline render of the BUILT archive holds real content at every cell the
console draws black (render mean 119, 154, 121 ... against screenshot 0.4).
So the archive is right and the runtime is not drawing it. That is HANDOFF-121
section 6's family of hypotheses, but section 6 measured the wrong cells: the
archive-black cells sit at dx -224 / +208, which are OFF SCREEN. The visible
field is dx -213..212 (measured by aligning the render to the screenshot).

This lists, for each cell named on the command line in SCREEN coordinates,
every layer's tile that covers it, with page slot, fx page, depth and load
position -- the test that separates "one late slot never allocated" from
"still data".

    python3 diag_screenblack.py md8_1 17:49 17:193 1217:1 ...
    python3 diag_screenblack.py md8_1 --auto screenshot.png
"""
from __future__ import annotations

import argparse
import collections
import os
import struct
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC              # noqa: E402
import lgp                            # noqa: E402

TILE = 16
UV_SCALE = 10_000_000
T_DSTX, T_DSTY, T_ID, T_PAL, T_TEX, T_TEX2 = 2, 4, 8, 22, 32, 34
T_SRC_X_BIG = 42

# measured by aligning render_field's output to the 1280x720 capture
VIS_X0 = -213
VIS_Y0 = -120
SCALE = 3


def snap(v, origin):
    """md8_1's tile grid is dx = -224 + 16k, dy = -120 + 16k -- the dy grid is
    offset by 8 from a multiple of 16, so snapping with floor(v/16)*16 misses
    every tile."""
    return int(np.floor((v - origin) / TILE)) * TILE + origin


def screen_to_field(sx, sy):
    return (snap(sx / SCALE + VIS_X0, -224),
            snap(sy / SCALE + VIS_Y0, -120))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('cells', nargs='*', help='screen_x:screen_y')
    ap.add_argument('--flevel',
                    default='sdout/atmosphere/contents/0100A5B00BDC6000/'
                            'romfs/ff7/workingdir/data/field/flevel.lgp')
    ap.add_argument('--field-coords', action='store_true',
                    help='cells are already dx:dy in field units')
    a = ap.parse_args(argv)

    want = set()
    for spec in a.cells:
        u, v = (int(t) for t in spec.split(':'))
        want.add((u, v) if a.field_coords else screen_to_field(u, v))

    A = lgp.Archive(os.path.join(_HERE, a.flevel))
    parts = lgp.split_sections(A.decompressed(A.index[a.field]))
    sec9 = parts[8]
    pages, tex_start, _e, px = DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    order = sorted(pmap)
    print(f'{a.field}: page_px={px} slots={order}')
    for s in order:
        p = pmap[s]
        print(f'    slot {s:>3}  depth {p.depth}  '
              f'load_pos {order.index(s) + 1}/{len(order)}')

    cover = collections.defaultdict(list)
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        for o in offs:
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            dy = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            key = (snap(dx, -224), snap(dy, -120))
            if key not in want:
                continue
            slot = sec9[o + T_TEX]
            fx = sec9[o + T_TEX2]
            eff = fx if (fx and fx in pmap) else slot
            p = pmap.get(eff)
            grid = 8 if (p is not None and p.size_flag) else 16
            step = 256 // grid
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            cover[key].append({
                'layer': layer, 'slot': slot, 'fx': fx, 'eff': eff,
                'pal': sec9[o + T_PAL],
                'depth': p.depth if p is not None else None,
                'pos': order.index(eff) + 1 if eff in order else None,
                'sx': int(round(u / UV_SCALE * grid)) * step,
                'sy': int(round(v / UV_SCALE * grid)) * step,
                'id': struct.unpack_from('<H', sec9, o + T_ID)[0],
            })

    hist = collections.Counter()
    fxhist = collections.Counter()
    for key in sorted(want):
        rows = cover.get(key, [])
        print(f'\n=== field cell {key}  ({len(rows)} tile(s)) ===')
        for r in sorted(rows, key=lambda d: d['layer']):
            print(f"    layer {r['layer']}  slot {r['slot']:>3} "
                  f"fx {r['fx']:>3} eff {r['eff']:>3} "
                  f"depth {r['depth']} load_pos {r['pos']}/{len(order)} "
                  f"pal {r['pal']:>3} src ({r['sx']},{r['sy']}) id {r['id']}")
            hist[r['eff']] += 1
            if r['fx']:
                fxhist[r['fx']] += 1

    print('\n-- effective page slot over all covering tiles --')
    for s, n in hist.most_common():
        print(f'    slot {s:>3}  load_pos {order.index(s) + 1}/{len(order)}'
              f'  depth {pmap[s].depth}   {n} tile(s)')
    if fxhist:
        print('-- fx pages seen --')
        for s, n in fxhist.most_common():
            print(f'    fx {s:>3}  {n} tile(s)  '
                  f'{"present" if s in pmap else "ABSENT FROM THIS FIELD"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
