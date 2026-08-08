#!/usr/bin/env python3
"""
diag_seam43.py -- MEASURE THE SEAM ITSELF, NOT THE COVERAGE.

`diag_edge43` showed recovery is flat right up to the 4:3 boundary, so the
tiles either side ARE drawing Cosmos. That kills "the interior is still
vanilla" as the explanation and leaves the question the user actually asked:
why is there a visible step at x = +/-160?

This measures the step directly, and against a control.

For every horizontally ADJACENT pair of layer-1 tiles that straddle the
boundary -- one ending at |dx| = 160, one starting there -- take the LAST
pixel column of the inner tile and the FIRST pixel column of the outer one and
compare them:

    step_built    from the BUILT archive: the pages, palettes and cells that
                  actually ship
    step_cosmos   the same two cells taken from Cosmos's own DDS, box-filtered
                  1024 -> 256: what FFNx puts on screen

    step_within   the mean column-to-column difference INSIDE those same two
                  tiles -- the field's own local texture, i.e. how big a step
                  is normal here

A seam that is in the DATA has step_built >> step_cosmos. A seam that is in
the RENDERER has step_built ~= step_cosmos, because the two sides already join
in the archive and something downstream is pulling them apart.

`step_within` is the control that makes the other two readable: a noisy field
has large adjacent-column differences everywhere and a large step at the
boundary means nothing there.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_interior43 as D                                    # noqa: E402
import lgp                                                     # noqa: E402

TILE = 16
HALF_43 = 160


def _cols(rgb, op):
    """(first column, last column) as float RGB, or None if either is keyed."""
    if rgb is None:
        return None
    if not op[:, 0].any() or not op[:, -1].any():
        return None
    return rgb[:, 0], rgb[:, -1]


def run(dump, built, art_dir, fields, quiet=False):
    A, B = lgp.Archive(dump), lgp.Archive(built)
    art = D.CosmosArt(art_dir)
    rows = []

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
        # index the BUILT field's layer-1 tiles by their destination
        bydst = {}
        for key, t in vb.items():
            if t.layer == 1:
                bydst[(t.dx, t.dy)] = t
        built_steps, cos_steps, within = [], [], []
        for side in (+1, -1):
            inner_dx = HALF_43 - TILE if side > 0 else -HALF_43
            outer_dx = HALF_43 if side > 0 else -HALF_43 - TILE
            for (dx, dy), ti in list(bydst.items()):
                if dx != inner_dx:
                    continue
                to = bydst.get((outer_dx, dy))
                if to is None:
                    continue
                Si, opi = D.tile_rgb(ti, pb, ab, cb)
                So, opo = D.tile_rgb(to, pb, ab, cb)
                ci, co = _cols(Si, opi) or (None, None), _cols(So, opo) or (None, None)
                if ci[0] is None or co[0] is None:
                    continue
                # the two columns that touch: inner's LAST, outer's FIRST
                # (mirrored on the left, where outer is to the LEFT of inner)
                if side > 0:
                    b_in, b_out = ci[1], co[0]
                else:
                    b_in, b_out = ci[0], co[1]
                built_steps.append(float(np.abs(b_in - b_out).mean()))
                if Si is not None:
                    d = np.abs(np.diff(Si.astype(np.float64), axis=1))
                    within.append(float(d.mean()))
                # the control: the same two cells straight out of Cosmos
                gi, _ = art.get(ti.slot, ti.pal)
                go, _ = art.get(to.slot, to.pal)
                # ti/to here are the BUILT tiles, whose slot numbering the
                # repack changed; use the DUMP tile at the same destination
                # for the inner one, which is the numbering Cosmos names.
                ta = va.get((1, inner_dx, dy))
                tb2 = va.get((1, outer_dx, dy))
                if ta is None or tb2 is None:
                    continue
                gi, _ = art.get(ta.slot, ta.pal)
                go, _ = art.get(tb2.slot, tb2.pal)
                if gi is None or go is None:
                    continue
                Ci = gi[ta.sy:ta.sy + TILE, ta.sx:ta.sx + TILE]
                Co = go[tb2.sy:tb2.sy + TILE, tb2.sx:tb2.sx + TILE]
                if Ci.shape[:2] != (TILE, TILE) or Co.shape[:2] != (TILE, TILE):
                    continue
                if side > 0:
                    c_in, c_out = Ci[:, -1], Co[:, 0]
                else:
                    c_in, c_out = Ci[:, 0], Co[:, -1]
                cos_steps.append(float(np.abs(c_in - c_out).mean()))
        if not built_steps:
            continue
        rows.append((name, len(built_steps), float(np.mean(built_steps)),
                     float(np.mean(cos_steps)) if cos_steps else float('nan'),
                     float(np.mean(within)) if within else float('nan')))

    rows.sort(key=lambda r: -(r[2] - (r[4] if r[4] == r[4] else 0)))
    print('%-10s %6s %10s %10s %10s   %s'
          % ('field', 'pairs', 'BUILT', 'COSMOS', 'within', 'verdict'))
    print('-' * 74)
    for n, k, sb, sc, sw in rows[:30]:
        v = ('DATA seam' if sc == sc and sb > sc * 1.8 + 4
             else 'joins as well as Cosmos does')
        print('%-10s %6d %10.1f %10.1f %10.1f   %s' % (n, k, sb, sc, sw, v))
    if rows:
        arr = np.array([[r[2], r[3], r[4]] for r in rows], float)
        print('-' * 74)
        print('%-10s %6d %10.1f %10.1f %10.1f'
              % ('MEAN', sum(r[1] for r in rows),
                 np.nanmean(arr[:, 0]), np.nanmean(arr[:, 1]),
                 np.nanmean(arr[:, 2])))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dump', required=True)
    ap.add_argument('--built', required=True)
    ap.add_argument('--art', required=True)
    ap.add_argument('--fields', nargs='*')
    ap.add_argument('--every', type=int, default=0)
    a = ap.parse_args()
    fields = a.fields
    if not fields:
        A = lgp.Archive(a.dump)
        fields = [n for n in A.names() if A.is_field(A.index[n])]
        if a.every:
            fields = fields[::a.every]
    run(a.dump, a.built, a.art, fields)


if __name__ == '__main__':
    main()
