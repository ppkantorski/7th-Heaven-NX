#!/usr/bin/env python3
"""
diag_interior43.py -- HOW MUCH COSMOS ART REACHES THE 4:3 PICTURE?

One question, per layer-1 tile lying wholly inside the 4:3 picture, measured
by rendering the same tile three ways and comparing pixels:

    V  vanilla  -- the dump's page through the dump's palette
    S  shipped  -- the built page through the built palette (what is ON SCREEN)
    C  Cosmos   -- the mod's DDS for that (field, page, palette), box-filtered
                   1024 -> 256, i.e. EXACTLY what a correct downscale-and-apply
                   would put there

Two numbers per tile:

    headroom  = mean |C - V|      how different Cosmos is from vanilla at all
    recovery  = 1 - |S-C|/|V-C|   how much of that difference reached the screen

        recovery 1.0  the tile is Cosmos
        recovery 0.0  the tile is still vanilla
        recovery < 0  the tile changed AWAY from Cosmos -- a defect

Only tiles with headroom > MIN_HEADROOM are scored, because a tile where
Cosmos and vanilla already agree cannot look different however well the
pipeline works, and averaging those in flatters the result.

Tiles are joined between dump and build on (layer, dx, dy): HANDOFF-67 s7.7
measured that no vanilla tile position is removed or relocated, so the join is
exact. `--csv` writes a per-tile row so any claim here can be re-checked.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from collections import Counter

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import dds_decode                                              # noqa: E402
import diag_common as DC                                       # noqa: E402
import ff7nx_marginart as MA                                   # noqa: E402
import ff7nx_marginblack as MB                                 # noqa: E402
import lgp                                                     # noqa: E402

TILE = 16
SECTION_PALETTE = 3
SECTION9 = 8
MIN_HEADROOM = 8.0     # /255 mean per-channel; below this the tile cannot
                       # visibly change no matter what the pipeline does


def tiles_of(raw):
    parts = lgp.split_sections(raw)
    cols, hdr, npg, cpp = MB.palette_colours(parts[SECTION_PALETTE])
    surv = DC.survey(parts[SECTION9])
    pages = {p.slot: p for p in surv['pages']}
    arrays = {s: MB.page_array(p) for s, p in pages.items()}
    out = {}
    for t in MB.read_tiles(parts[SECTION9], surv, pages):
        out[(t.layer, t.dx, t.dy)] = t
    return out, pages, arrays, cols, surv


def tile_rgb(t, pages, arrays, cols):
    """
    (rgb, opaque) for the 16x16 this tile draws, or (None, None).

    `opaque` is the KEY. Index 0 on a paletted page, and 0x0000 on a truecolor
    one, are the transparency colour key -- the engine draws nothing there and
    whatever is behind shows through. Rendering them through palette entry 0
    paints them in whatever colour that slot happens to hold, which on `qc` is
    a vivid yellow and on `mtnvl3` a vivid cyan. Comparing those pixels against
    the mod's DDS -- which is opaque art there -- invents an enormous
    difference that is not on screen, and `ff7nx_marginart` is right not to
    touch them (it preserves the zero-mask exactly, `keep0`).

    So every comparison below is masked to pixels that are actually drawn.
    """
    p = pages.get(t.slot)
    a = arrays.get(t.slot)
    if p is None or a is None:
        return None, None
    arr, k = a
    blk = MB.source_block(arr, k, t.sx, t.sy)
    if blk is None:
        return None, None
    if p.depth == 1:
        if t.pal >= cols.shape[0]:
            return None, None
        return MA.palette_rgb(cols[t.pal])[blk].astype(np.float64), blk != 0
    v = blk.astype(np.uint32)
    r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    rgb = np.stack([(r << 3) | (r >> 2),
                    (g << 2) | (g >> 4),
                    (b << 3) | (b >> 2)], -1).astype(np.float64)
    op = blk != 0
    if k > 1:
        rgb = rgb.reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3))
        op = op.reshape(TILE, k, TILE, k).mean(axis=(1, 3)) > 0.5
    return rgb, op


class CosmosArt:
    def __init__(self, art_dir):
        self.dir = art_dir
        self._field = None
        self._shipped = {}
        self._cache = {}

    def open(self, field):
        if self._field == field:
            return
        self._field, self._cache, self._shipped = field, {}, {}
        for f in glob.glob(os.path.join(self.dir, field, '%s_*.dds' % field)):
            bits = os.path.basename(f)[:-4].rsplit('_', 2)
            if len(bits) != 3:
                continue
            try:
                self._shipped.setdefault(int(bits[1]), {})[int(bits[2])] = f
            except ValueError:
                continue

    def get(self, page, pal):
        avail = self._shipped.get(page)
        if not avail:
            return None, None
        q = pal if pal in avail else sorted(avail)[0]
        key = (page, q)
        if key not in self._cache:
            rgba, w, h = dds_decode.decode_dds(open(avail[q], 'rb').read())
            a = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
            rgb = a[..., :3].astype(np.float64)
            rgb[a[..., 3] < 8] = 0
            k = w // 256
            if k < 1 or w != 256 * k or h != 256 * k:
                self._cache[key] = None
            else:
                self._cache[key] = rgb.reshape(256, k, 256, k, 3).mean((1, 3))
        return self._cache[key], q


def run(dump, built, art_dir, fields, quiet=False, csv_path=None):
    A, B = lgp.Archive(dump), lgp.Archive(built)
    art = CosmosArt(art_dir)
    rows = []
    tot = Counter()
    allrec, allhead = [], []
    per_field = []

    for name in fields:
        ea, eb = A.index.get(name), B.index.get(name)
        if ea is None or eb is None:
            continue
        try:
            va, pa, aa, ca, sa = tiles_of(A.decompressed(ea))
            vb, pb, ab, cb, sb = tiles_of(B.decompressed(eb))
        except Exception as exc:                               # noqa: BLE001
            tot['skip'] += 1
            if not quiet:
                print('%-9s SKIP %s' % (name, exc))
            continue
        art.open(name)
        rec, head = [], []
        c = Counter()
        for key, ta in va.items():
            if ta.layer != 1 or ta.outside_43:
                continue
            tb = vb.get(key)
            if tb is None:
                c['gone'] += 1
                continue
            V, OP = tile_rgb(ta, pa, aa, ca)
            S, _ = tile_rgb(tb, pb, ab, cb)
            if V is None or S is None:
                c['unreadable'] += 1
                continue
            c['tiles'] += 1
            # MASKED TO THE PIXELS THE ENGINE ACTUALLY DRAWS. See tile_rgb.
            if OP.sum() < 0.25 * TILE * TILE:
                c['mostly_keyed'] += 1
                continue
            img, q = art.get(ta.slot, ta.pal)
            if img is None:
                c['no_dds'] += 1
                continue
            C = img[ta.sy:ta.sy + TILE, ta.sx:ta.sx + TILE]
            if C.shape[:2] != (TILE, TILE):
                c['no_dds'] += 1
                continue
            dcv = float(np.abs(C - V)[OP].mean())
            head.append(dcv)
            if dcv <= MIN_HEADROOM:
                c['flat'] += 1
                continue
            dsc = float(np.abs(S - C)[OP].mean())
            dsv = float(np.abs(S - V)[OP].mean())
            r = 1.0 - dsc / dcv
            rec.append(r)
            c['scored'] += 1
            if dsv <= 1.0:
                c['untouched'] += 1
            elif r < 0:
                c['worse'] += 1
            elif r >= 0.6:
                c['good'] += 1
            else:
                c['partial'] += 1
            if csv_path:
                rows.append((name, ta.dx, ta.dy, ta.slot, ta.pal, ta.sx, ta.sy,
                             tb.slot, pb[tb.slot].depth if tb.slot in pb else 0,
                             q, round(dcv, 2), round(dsc, 2), round(dsv, 2),
                             round(r, 3)))
        if not c['tiles']:
            continue
        tot.update(c)
        allrec += rec
        allhead += head
        m = float(np.mean(rec)) if rec else 0.0
        per_field.append((name, c['tiles'], c['scored'], m))
        if not quiet:
            print('%-9s int %5d  scored %5d  recovery %6.2f   '
                  'untouched %5d  worse %4d  no_dds %4d'
                  % (name, c['tiles'], c['scored'], m,
                     c['untouched'], c['worse'], c['no_dds']))

    n = tot['tiles'] or 1
    s = tot['scored'] or 1
    print()
    print('interior layer-1 tiles                     %8d' % tot['tiles'])
    print('  no Cosmos DDS for that page              %8d  (%.1f%%)'
          % (tot['no_dds'], 100.0 * tot['no_dds'] / n))
    print('  >75%% colour-keyed, nothing to compare  %8d  (%.1f%%)'
          % (tot['mostly_keyed'], 100.0 * tot['mostly_keyed'] / n))
    print('  Cosmos ~= vanilla anyway (headroom<=%.0f)  %8d  (%.1f%%)'
          % (MIN_HEADROOM, tot['flat'], 100.0 * tot['flat'] / n))
    print('  SCORED (real difference available)       %8d  (%.1f%%)'
          % (tot['scored'], 100.0 * tot['scored'] / n))
    if allrec:
        r = np.array(allrec)
        print()
        print('  of the scored tiles:')
        print('    still exactly vanilla                  %8d  (%.1f%%)'
              % (tot['untouched'], 100.0 * tot['untouched'] / s))
        print('    changed AWAY from Cosmos (recovery<0)  %8d  (%.1f%%)'
              % (tot['worse'], 100.0 * tot['worse'] / s))
        print('    partial       (0 <= recovery < 0.6)    %8d  (%.1f%%)'
              % (tot['partial'], 100.0 * tot['partial'] / s))
        print('    good          (recovery >= 0.6)        %8d  (%.1f%%)'
              % (tot['good'], 100.0 * tot['good'] / s))
        print()
        print('    MEAN RECOVERY                          %8.2f'
              '   (1.0 = Cosmos, 0.0 = vanilla)' % r.mean())
        print('    median recovery                        %8.2f'
              % float(np.median(r)))
    if csv_path:
        with open(csv_path, 'w', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(['field', 'dx', 'dy', 'vpage', 'pal', 'sx', 'sy',
                        'bpage', 'bdepth', 'dds_pal', 'headroom',
                        'd_shipped_cosmos', 'd_shipped_vanilla', 'recovery'])
            w.writerows(rows)
        print('\nwrote %s (%d rows)' % (csv_path, len(rows)))
    return tot, per_field


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dump', required=True)
    ap.add_argument('--built', required=True)
    ap.add_argument('--art', required=True)
    ap.add_argument('--fields', nargs='*')
    ap.add_argument('-n', type=int, default=0)
    ap.add_argument('--csv')
    ap.add_argument('-q', action='store_true')
    a = ap.parse_args()
    if not os.path.isdir(a.art):
        raise SystemExit('--art is not a directory: %r  '
                         '(quote it, do not backslash-escape spaces)' % a.art)
    fields = a.fields
    if not fields:
        A = lgp.Archive(a.dump)
        fields = [n for n in A.names() if A.is_field(A.index[n])]
        if a.n:
            fields = fields[:a.n]
    run(a.dump, a.built, a.art, fields, a.q, a.csv)


if __name__ == '__main__':
    main()
