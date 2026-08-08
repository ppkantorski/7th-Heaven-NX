#!/usr/bin/env python3
"""
diag_edge43.py -- IS THE SEAM AT THE 4:3 EDGE A COVERAGE HOLE?

The user's report, and it is a sharp one:

    "on the windows version using ffnx the 4:3 edges blend smoothly, there is
     no abrupt discontinuity at the 4:3 edge. the cosmos limit break upscaled
     textures are not 1:1 representations of the original textures, the
     portions at / near the 4:3 region were tweaked so they blend smoothly and
     seamlessly. even at 256 px we shouldnt be seeing this abrupt texture
     discontinuity."

That is a testable claim with a clear prediction. If Cosmos retouched the art
either side of the boundary so it joins, then a build that used Cosmos
EVERYWHERE cannot show a seam however soft the downscale is. A seam therefore
means the tiles immediately INSIDE the boundary are not drawing Cosmos.

So: `diag_interior43`'s recovery, binned by distance from the 4:3 edge.

    |dx| is the tile's field-space x. HALF_43 = 160, so a tile at |dx| = 144
    is the last one wholly inside the picture and the one that has to match
    the margin tile at |dx| = 160.

If recovery is flat across the bins, the seam is not a coverage hole and the
explanation is elsewhere. If it collapses in the outermost interior bins, the
tiles that have to join the margin are exactly the ones still drawing vanilla,
and that is the seam.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_interior43 as D                                    # noqa: E402
import ff7nx_marginblack as MB                                 # noqa: E402
import lgp                                                     # noqa: E402

TILE = 16
HALF_43 = MB.HALF_43 if hasattr(MB, 'HALF_43') else 160
BINS = [(0, 48), (48, 80), (80, 112), (112, 144), (144, 160)]


def run(dump, built, art_dir, fields, quiet=False):
    A, B = lgp.Archive(dump), lgp.Archive(built)
    art = D.CosmosArt(art_dir)
    rec = defaultdict(list)
    why = defaultdict(Counter)

    for name in fields:
        ea, eb = A.index.get(name), B.index.get(name)
        if ea is None or eb is None:
            continue
        try:
            va, pa, aa, ca, _ = D.tiles_of(A.decompressed(ea))
            vb, pb, ab, cb, _ = D.tiles_of(B.decompressed(eb))
        except Exception:                                      # noqa: BLE001
            continue
        art.open(name)
        for key, ta in va.items():
            if ta.layer != 1 or ta.outside_43:
                continue
            tb = vb.get(key)
            if tb is None:
                continue
            # The tile's own span, so a tile straddling the boundary is
            # counted by the edge it reaches, not by its left corner.
            d = min(abs(ta.dx), abs(ta.dx + TILE))
            if ta.dx < 0 < ta.dx + TILE:
                d = 0
            b = next((i for i, (lo, hi) in enumerate(BINS) if lo <= d < hi),
                     len(BINS) - 1)
            V, OP = D.tile_rgb(ta, pa, aa, ca)
            S, _ = D.tile_rgb(tb, pb, ab, cb)
            if V is None or S is None or OP.sum() < 0.25 * TILE * TILE:
                continue
            img, q = art.get(ta.slot, ta.pal)
            if img is None:
                why[b]['no_dds'] += 1
                continue
            C = img[ta.sy:ta.sy + TILE, ta.sx:ta.sx + TILE]
            if C.shape[:2] != (TILE, TILE):
                why[b]['no_dds'] += 1
                continue
            dcv = float(np.abs(C - V)[OP].mean())
            why[b]['tiles'] += 1
            if dcv <= D.MIN_HEADROOM:
                why[b]['flat'] += 1
                continue
            r = 1.0 - float(np.abs(S - C)[OP].mean()) / dcv
            rec[b].append(r)
            why[b]['scored'] += 1
            if float(np.abs(S - V)[OP].mean()) <= 1.0:
                why[b]['vanilla'] += 1
            d2 = pb[tb.slot].depth if tb.slot in pb else 0
            why[b]['depth%d' % d2] += 1

    print('%-16s %8s %8s %8s %9s %9s %9s'
          % ('|dx| from centre', 'tiles', 'scored', 'vanilla',
             'recovery', 'depth1', 'depth2'))
    print('-' * 74)
    for i, (lo, hi) in enumerate(BINS):
        c, v = why[i], rec[i]
        if not c['tiles']:
            continue
        tag = '%3d-%3d%s' % (lo, hi, '  <- the edge' if hi == 160 else '')
        print('%-16s %8d %8d %8d %9.2f %9d %9d'
              % (tag, c['tiles'], c['scored'], c['vanilla'],
                 float(np.mean(v)) if v else 0.0, c['depth1'], c['depth2']))
    print('-' * 74)
    allv = [x for i in rec for x in rec[i]]
    print('%-16s %8d %8d %8d %9.2f'
          % ('ALL INTERIOR', sum(why[i]['tiles'] for i in why),
             sum(why[i]['scored'] for i in why),
             sum(why[i]['vanilla'] for i in why),
             float(np.mean(allv)) if allv else 0.0))
    return rec, why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dump', required=True)
    ap.add_argument('--built', required=True)
    ap.add_argument('--art', required=True)
    ap.add_argument('--fields', nargs='*')
    ap.add_argument('--every', type=int, default=0,
                    help='sample every Nth field instead of all')
    a = ap.parse_args()
    fields = a.fields
    if not fields:
        A = lgp.Archive(a.dump)
        fields = [n for n in A.names() if A.is_field(A.index[n])]
        if a.every:
            fields = fields[::a.every]
    print('%d field(s)' % len(fields))
    run(a.dump, a.built, a.art, fields)


if __name__ == '__main__':
    main()
