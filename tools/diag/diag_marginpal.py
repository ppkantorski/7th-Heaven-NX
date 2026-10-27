#!/usr/bin/env python3
"""
diag_marginpal.py -- does the palette a MARGIN tile names fit the art Cosmos
ships for it?

THE QUESTION
============
Cosmos authors its 16:9 margin extension as tiles pointing at FLAT placeholder
cells -- one palette index over the whole 16x16 -- and ships the real art as an
external DDS. Under FFNx that art REPLACES the page and the tile's palette is
never applied (saveload.cpp:138, HANDOFF-80 4.1), so the palette byte on a
margin tile is unconstrained: the mod had no reason to get it right.

We have no per-palette 8-bit path. `ff7nx_marginart` quantises Cosmos's RGB
against the palette THE TILE NAMES. If that palette cannot represent the art,
the cell collapses to a handful of indices -- or one -- and lands on screen as
a flat block.

    python3 diag_marginpal.py [--fields md8_1,mrkt1] [--limit 30]

Prints, per field and in total, the mean quantisation error and index count for
the art at the NAMED palette against the BEST palette in the same field.

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
import argparse
import os
import sys
from collections import Counter

import numpy as np

_HERE = nxpaths.ROOT
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import dds_decode                 # noqa: E402
import diag_common as DC          # noqa: E402
import ff7nx_marginart as MA      # noqa: E402
import ff7nx_marginblack as MB    # noqa: E402
import field_bg_repack as R       # noqa: E402
import lgp                        # noqa: E402

MOD9 = 'cache/CosmosLimitBreak/LIMIT BREAK/flevel.lgp'
IRO = 'mods/CosmosLimitBreak.iro'
VAN = 'dump/romfs/ff7/workingdir/data/field/flevel.lgp'
TILE = 16
EMPTY_MAX = 24            # ff7nx_marginart.fill_field's EMPTY SOURCE test


def mod_margin_tiles(name, V):
    """[(slot, sx, sy, pal)] for the mod's FLAT layer-1 tiles vanilla lacks."""
    path = os.path.join(MOD9, name + '.chunk.9')
    if not os.path.exists(path):
        return None, None
    sec9 = open(path, 'rb').read()
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    tiles = MB.read_tiles(sec9, surv, pages)
    ve = V.index.get(name)
    if ve is None:
        return None, None
    vs9 = lgp.split_sections(V.decompressed(ve))[8]
    vsurv = DC.survey(vs9)
    vpg = {p.slot: p for p in vsurv['pages']}
    vdst = {(t.dx, t.dy) for t in MB.read_tiles(vs9, vsurv, vpg)
            if t.layer == 1}
    out = []
    for t in tiles:
        if t.layer != 1 or (t.dx, t.dy) in vdst:
            continue
        p = pages.get(t.slot)
        if p is None or p.depth != 1:
            continue
        a = MB.page_array(p)
        b = MB.source_block(a[0], a[1], t.sx, t.sy)
        if b is not None and np.unique(b).size == 1:
            out.append((t.slot, t.sx, t.sy, t.pal))
    # palettes the PICTURE (inside 4:3) uses, most common first
    pic = Counter(t.pal for t in tiles if t.layer == 1 and not t.outside_43)
    return out, pic


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--fields')
    ap.add_argument('--limit', type=int, default=30)
    a = ap.parse_args(argv)

    V = lgp.Archive(VAN)
    reader = R.IroReader(IRO)
    idx = R.index_field_dds(
        [n for n in reader.by_name if n.endswith('.dds')])
    # collapse to one entry per (field, page, palette)
    pick = {k: sorted(v)[0] for k, v in idx.items()}

    if a.fields:
        names = a.fields.split(',')
    else:
        cand = []
        for fn in sorted(os.listdir(MOD9)):
            cand.append(fn[:-len('.chunk.9')])
        names = cand

    cache = {}

    def art(field, page, pal):
        """(H,W,3) uint8 or None, and the palette it was actually drawn at."""
        for p in (pal, 0):
            key = (field, page, p)
            nm = pick.get(key)
            if nm is None:
                continue
            if key not in cache:
                try:
                    rgba, w, h = dds_decode.decode_dds(reader.read(nm))
                    im = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
                    cache[key] = im[..., :3].copy()
                except Exception:                          # noqa: BLE001
                    cache[key] = None
            if cache[key] is not None:
                return cache[key], p
        return None, None

    T = dict(cells=0, empty=0, noart=0, scored=0,
             refused_named=0, refused_best=0)
    err_named, err_best, n_named, n_best = [], [], [], []
    rows = []
    done = 0
    for name in names:
        tiles, pic = mod_margin_tiles(name, V)
        if not tiles:
            continue
        ve = V.index.get(name)
        cols, hdr, npg, cpp = MB.palette_colours(
            lgp.split_sections(V.decompressed(ve))[3])
        prgbs = [MA.palette_rgb(cols[p]) for p in range(npg)]
        f = dict(cells=0, scored=0, en=[], eb=[], nn=[], nb=[],
                 rn=0, rb=0, best=Counter())
        for slot, sx, sy, pal in tiles:
            T['cells'] += 1
            f['cells'] += 1
            img, _src = art(name, slot, pal)
            if img is None:
                T['noart'] += 1
                continue
            k = img.shape[1] // 256
            if k < 1:
                T['noart'] += 1
                continue
            blk = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
            if blk.shape[:2] != (TILE * k, TILE * k):
                T['noart'] += 1
                continue
            small = blk.reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3))
            if small.max() <= EMPTY_MAX:
                T['empty'] += 1
                continue
            small = small.astype(np.uint8)
            best_e, best_p, best_n = None, None, None
            for p in range(npg):
                if pal >= npg:
                    break
                ix = MA.quantise(small, prgbs[p])
                e = float(np.abs(prgbs[p][ix].astype(np.int16)
                                 - small.astype(np.int16)).mean())
                nn = int(np.unique(ix).size)
                if p == pal:
                    en, nnamed = e, nn
                if best_e is None or e < best_e:
                    best_e, best_p, best_n = e, p, nn
            if best_e is None or pal >= npg:
                T['noart'] += 1
                continue
            T['scored'] += 1
            f['scored'] += 1
            f['en'].append(en)
            f['eb'].append(best_e)
            f['nn'].append(nnamed)
            f['nb'].append(best_n)
            f['best'][best_p] += 1
            if en > MA.MAX_QUANT_ERR:
                T['refused_named'] += 1
                f['rn'] += 1
            if best_e > MA.MAX_QUANT_ERR:
                T['refused_best'] += 1
                f['rb'] += 1
        if f['scored']:
            err_named += f['en']
            err_best += f['eb']
            n_named += f['nn']
            n_best += f['nb']
            rows.append((name, f, dict(pic.most_common(2))))
        cache.clear()
        done += 1
        if done >= a.limit:
            break

    print('%-10s %6s | %-22s | %-22s | %s'
          % ('field', 'cells', 'NAMED palette', 'BEST palette', 'best pal'))
    for name, f, pic in sorted(rows, key=lambda r: -(
            np.mean(r[1]['en']) - np.mean(r[1]['eb']))):
        print('%-10s %6d | err %5.1f  n %5.1f  ref %-4d | '
              'err %5.1f  n %5.1f  ref %-4d | %s'
              % (name, f['scored'],
                 np.mean(f['en']), np.mean(f['nn']), f['rn'],
                 np.mean(f['eb']), np.mean(f['nb']), f['rb'],
                 dict(f['best'].most_common(3))))
    print()
    print('fields scored          %d' % len(rows))
    print('placeholder cells      %d' % T['cells'])
    print('  no Cosmos art        %d' % T['noart'])
    print('  art is near-black    %d  (EMPTY SOURCE, correctly left dark)'
          % T['empty'])
    print('  SCORED               %d' % T['scored'])
    if T['scored']:
        print()
        print('                       named palette      best palette')
        print('  mean quant error     %12.2f   %12.2f'
              % (np.mean(err_named), np.mean(err_best)))
        print('  mean indices used    %12.2f   %12.2f'
              % (np.mean(n_named), np.mean(n_best)))
        print('  cells collapsed to 1 %12d   %12d'
              % (sum(1 for x in n_named if x == 1),
                 sum(1 for x in n_best if x == 1)))
        print('  cells under 8 idx    %12d   %12d'
              % (sum(1 for x in n_named if x < 8),
                 sum(1 for x in n_best if x < 8)))
        print('  REFUSED (err > %d)   %12d   %12d'
              % (MA.MAX_QUANT_ERR, T['refused_named'], T['refused_best']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
