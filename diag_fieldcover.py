#!/usr/bin/env python3
"""
diag_fieldcover.py -- how much CONTIGUOUS art has each field got, and is it
                      in the built pages, and can the camera reach it?

    python3 diag_fieldcover.py <built flevel.lgp>
    python3 diag_fieldcover.py <built flevel.lgp> md8_1 mkt_ia trnad_1
    python3 diag_fieldcover.py <built flevel.lgp> --csv field_cover.csv

WHY THIS REPLACES `diag_fieldwidth.py` / `wide_art_span.csv`
============================================================
`diag_fieldwidth` measures `max(tile.x) - min(tile.x)`. That is not the art;
it is the bounding box, and on this archive it is wrong by a factor of
twenty on real fields:

    nivinn_2   bounding box 10272     contiguous art 512
    cos_btm    bounding box 10272     contiguous art 512

A single stray tile ten thousand units away makes a 512-unit field look like
a 10,272-unit one. Any gate built on that number is built on sand.

This walks layer 1 and layer 2, turns every tile into the column interval
`[x, x+16)`, MERGES them, and reports the contiguous run that contains the
camera-range midpoint. That is the art a camera can actually traverse
without crossing a hole.

It then answers the three questions that decide whether a field can fill a
16:9 frame, separately, because they have three different fixes:

  1. IS THE ART THERE?        contiguous span >= 428
  2. IS IT IN THE PAGES?      each margin tile's 16x16 cell sampled in the
                              page it points at -- a tile pointing at a
                              blank cell draws nothing and shows whatever
                              the buffer held. This is the HANDOFF-60 §3.6
                              page-coverage failure, measured per field
                              instead of assumed.
  3. CAN THE CAMERA REACH IT? the baked section-8 range against the art.

WHAT IT FOUND ON THE SHIPPING BUILD (2026-08, 709 fields)
=========================================================
```
contiguous art >= 428 : 608        <- not 340. HANDOFF-60 §3.8 is stale.
   <= 320 (4:3 only)  :  69
   321-427 (short)    :  32

camera range >= 428   : 110        <- 498 fields have the art and no range
```

and spot checks:

```
md8_1   range -160..160 (320)   art -224..224 (448)   margin tiles: 134 art, 14 blank
md8_2   range -266..266 (532)   art -320..320 (640)   margin tiles: 880 art, 14 blank
mkt_ia  range -160..160 (320)   art -112..144 (256)   margin tiles: none at all
```

`mkt_ia` has 256 units of art. That is less than 4:3 needs. It is the WORST
possible field to test a widescreen change against and it has been the test
field for several sessions.
"""
from __future__ import annotations

import argparse
import collections
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lgp                                                      # noqa: E402
import diag_common as DC                                        # noqa: E402
import field_bg_native as FN                                    # noqa: E402
import field_bg_repack as RP                                    # noqa: E402

SECTION8, SECTION9 = 7, 8
NEEDED = 428            # 640 / WS_SCALE / 2 at the shipping field buffer
HALF = NEEDED // 2      # 214 -- what the camera needs each side
STOCK_HALF = 160        # the 4:3 half-view, and the module's `#0xa0`
TILE = 16
MARGIN_FROM = 160       # a tile beyond +/-160 is outside the 4:3 picture


def merged_columns(sec9, surv):
    """[(x0, x1)] -- layer 1+2 tile coverage, merged into contiguous runs."""
    iv = []
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer not in (1, 2):
            continue
        for o in offs:
            x = struct.unpack_from('<h', sec9, o + DC.TILE_DST_X)[0]
            iv.append((x, x + TILE))
    iv.sort()
    out = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def cell_has_art(page, cx, cy, px, step=4):
    """Sample a 16x16 cell. False when every sample is the empty value."""
    d = page.data
    for yy in range(0, TILE, step):
        row = (cy + yy) * px
        for xx in range(0, TILE, step):
            i = row + cx + xx
            if page.depth == 2:
                if i * 2 + 2 > len(d):
                    return True         # cannot sample -> do not accuse it
                if struct.unpack_from('<H', d, i * 2)[0] != FN.EMPTY:
                    return True
            else:
                if i >= len(d):
                    return True
                if d[i]:
                    return True
    return False


def measure_field(sec8, sec9):
    """One row. Raises on anything it cannot read."""
    left, top, right, bottom = struct.unpack_from('<4h', sec8, 12)
    surv = DC.survey(sec9)
    px = surv['page_px']
    pages = {p.slot: p for p in surv['pages']}
    runs = merged_columns(sec9, surv)
    if not runs:
        raise ValueError('no layer 1/2 tiles')
    mid = (left + right) // 2
    run = next((r for r in runs if r[0] <= mid <= r[1]), None) \
        or max(runs, key=lambda r: r[1] - r[0])

    art = blank = nopage = 0
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer not in (1, 2):
            continue
        for o in offs:
            x = struct.unpack_from('<h', sec9, o + DC.TILE_DST_X)[0]
            if abs(x + TILE // 2) <= MARGIN_FROM:
                continue                       # inside the 4:3 picture
            p = pages.get(sec9[o + RP.T_TEXID])
            if p is None:
                nopage += 1
                continue
            u, v = struct.unpack_from('<II', sec9, o + RP.T_SRC_X_BIG)
            cx, cy = int(round(u / 1e7 * px)), int(round(v / 1e7 * px))
            if cell_has_art(p, cx, cy, px):
                art += 1
            else:
                blank += 1

    span = run[1] - run[0]
    # The bounds the STOCK +/-160 clamp code will produce from this range.
    lo, hi = left + STOCK_HALF, right - STOCK_HALF
    if lo > hi:
        lo = hi = (lo + hi) // 2
    # Worst-case uncovered width, over every reachable camera position.
    worst = max(max(0, run[0] - (c - HALF)) + max(0, (c + HALF) - run[1])
                for c in (lo, hi, (lo + hi) // 2))
    return {'left': left, 'right': right, 'range': right - left,
            'a0': run[0], 'a1': run[1], 'span': span, 'runs': len(runs),
            'bbox': runs[-1][1] - runs[0][0],
            'margin_art': art, 'margin_blank': blank, 'margin_nopage': nopage,
            'pages': surv['n_pages'], 'page_px': px,
            'cam_lo': lo, 'cam_hi': hi, 'worst_band': worst}


def verdict(r):
    if r['span'] <= 320:
        return 'ART ABSENT   (4:3 only, %d units)' % r['span']
    if r['span'] < NEEDED:
        return 'ART SHORT    (%d of %d)' % (r['span'], NEEDED)
    if r['margin_blank'] > r['margin_art']:
        return 'PAGES BLANK  (%d of %d margin tiles draw nothing)' % (
            r['margin_blank'], r['margin_blank'] + r['margin_art'])
    if r['worst_band']:
        return 'CAMERA WALKS OFF (%d units bare at the extremes)' % (
            r['worst_band'])
    return 'OK           (art, pages and camera all reach)'


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('flevel')
    ap.add_argument('fields', nargs='*')
    ap.add_argument('--csv', metavar='PATH')
    ap.add_argument('--worst', type=int, default=20)
    a = ap.parse_args(argv)

    only = {f.lower() for f in a.fields} or None
    print('reading %s ...' % a.flevel)
    arc = lgp.Archive(a.flevel)
    rows, bad = {}, []
    for name in sorted(arc.names()):
        e = arc.index.get(name)
        if e is None or not arc.is_field(e):
            continue
        if only and name.lower().split('.')[0] not in only:
            continue
        try:
            parts = lgp.split_sections(arc.decompressed(e))
            rows[name] = measure_field(parts[SECTION8], parts[SECTION9])
        except Exception as exc:                               # noqa: BLE001
            bad.append((name, str(exc)[:60]))

    if not rows:
        print('! nothing measured')
        for n, w in bad[:6]:
            print('   %-12s %s' % (n, w))
        return 2

    if only:
        for n in sorted(rows):
            r = rows[n]
            print()
            print('%-10s  %d page(s) at %dpx' % (n, r['pages'], r['page_px']))
            print('   camera range %5d .. %-5d  (%4d)   clamped to %d .. %d'
                  % (r['left'], r['right'], r['range'], r['cam_lo'],
                     r['cam_hi']))
            print('   contiguous art %5d .. %-5d  (%4d)   %d run(s), '
                  'bounding box %d'
                  % (r['a0'], r['a1'], r['span'], r['runs'], r['bbox']))
            print('   margin tiles: %d with art, %d BLANK, %d with no page'
                  % (r['margin_art'], r['margin_blank'], r['margin_nopage']))
            print('   %s' % verdict(r))
        for n, w in bad:
            print('%-10s  !! %s' % (n, w))
        return 0

    v = collections.Counter(verdict(r).split('(')[0].strip()
                            for r in rows.values())
    print()
    print('%d field(s) measured, %d unreadable' % (len(rows), len(bad)))
    print()
    print('  contiguous art >= %d : %4d'
          % (NEEDED, sum(1 for r in rows.values() if r['span'] >= NEEDED)))
    print('  321 .. %-3d (short)   : %4d'
          % (NEEDED - 1, sum(1 for r in rows.values()
                             if 320 < r['span'] < NEEDED)))
    print('  <= 320 (4:3 only)    : %4d'
          % sum(1 for r in rows.values() if r['span'] <= 320))
    print()
    print('  camera range >= %d   : %4d'
          % (NEEDED, sum(1 for r in rows.values() if r['range'] >= NEEDED)))
    print('  art the camera cannot reach: %4d field(s)'
          % sum(1 for r in rows.values()
                if r['span'] >= NEEDED and r['range'] < NEEDED))
    print()
    print('  margin tiles pointing at a BLANK cell: %d across %d field(s)'
          % (sum(r['margin_blank'] for r in rows.values()),
             sum(1 for r in rows.values() if r['margin_blank'])))
    print('  margin tiles pointing at a MISSING page: %d across %d field(s)'
          % (sum(r['margin_nopage'] for r in rows.values()),
             sum(1 for r in rows.values() if r['margin_nopage'])))
    print()
    for k, n in v.most_common():
        print('  %-18s %4d' % (k, n))

    print()
    print('worst page coverage in the margin, worst first:')
    worst = sorted((r for r in rows.items() if r[1]['margin_blank']),
                   key=lambda t: -t[1]['margin_blank'])[:a.worst]
    for n, r in worst:
        print('  %-12s %4d blank / %4d art   span %5d  range %5d'
              % (n, r['margin_blank'], r['margin_art'], r['span'],
                 r['range']))

    if a.csv:
        import csv
        with open(a.csv, 'w', newline='') as f:
            w = csv.writer(f)
            cols = ['field', 'left', 'right', 'range', 'cam_lo', 'cam_hi',
                    'a0', 'a1', 'span', 'runs', 'bbox', 'margin_art',
                    'margin_blank', 'margin_nopage', 'pages', 'page_px',
                    'worst_band', 'verdict']
            w.writerow(cols)
            for n in sorted(rows):
                r = rows[n]
                w.writerow([n] + [r[c] for c in cols[1:-1]] + [verdict(r)])
        print()
        print('wrote %s' % a.csv)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
