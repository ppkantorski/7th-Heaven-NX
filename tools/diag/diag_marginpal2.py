#!/usr/bin/env python3
"""
diag_marginpal2.py -- the same measurement as diag_marginpal.py, but scoring
the three choices that could actually SHIP.

A depth-1 page is drawn through ONE palette, so the palette cannot be chosen
per cell. The unit is the page. Four candidates:

    named       what the mod ships (the current behaviour)
    percell     the best palette for each cell -- NOT SHIPPABLE, the ceiling
    perpage     one palette per (field, page), minimising total error over
                that page's margin cells
    adjacent    the palette most used by the layer-1 PICTURE tiles of the
                field -- a semantic guess, no art decode needed to pick it

`perpage` is what a shippable pass would do. `percell` is printed only to show
how much of the ceiling `perpage` reaches.

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
from collections import Counter, defaultdict

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
from diag_marginpal import MOD9, IRO, VAN, EMPTY_MAX, TILE   # noqa: E402


def mod_field(name, V):
    path = os.path.join(MOD9, name + '.chunk.9')
    if not os.path.exists(path) or name not in V.index:
        return None
    sec9 = open(path, 'rb').read()
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    tiles = MB.read_tiles(sec9, surv, pages)
    vs9 = lgp.split_sections(V.decompressed(V.index[name]))[8]
    vsurv = DC.survey(vs9)
    vpg = {p.slot: p for p in vsurv['pages']}
    vdst = {(t.dx, t.dy) for t in MB.read_tiles(vs9, vsurv, vpg)
            if t.layer == 1}
    ph = []
    for t in tiles:
        if t.layer != 1 or (t.dx, t.dy) in vdst:
            continue
        p = pages.get(t.slot)
        if p is None or p.depth != 1:
            continue
        a = MB.page_array(p)
        b = MB.source_block(a[0], a[1], t.sx, t.sy)
        if b is not None and np.unique(b).size == 1:
            ph.append((t.slot, t.sx, t.sy, t.pal))
    pic = Counter(t.pal for t in tiles if t.layer == 1 and not t.outside_43)
    return ph, pic


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=40)
    ap.add_argument('--fields')
    a = ap.parse_args(argv)

    V = lgp.Archive(VAN)
    reader = R.IroReader(IRO)
    pick = {k: sorted(v)[0] for k, v in R.index_field_dds(
        [n for n in reader.by_name if n.endswith('.dds')]).items()}

    names = a.fields.split(',') if a.fields else \
        [f[:-len('.chunk.9')] for f in sorted(os.listdir(MOD9))]

    TAGS = ('named', 'percell', 'perpage', 'adjacent')
    tot = {t: dict(err=[], n=[], ref=0, one=0) for t in TAGS}
    nfields = nscored = 0
    worst = []

    for name in names:
        got = mod_field(name, V)
        if not got:
            continue
        ph, pic = got
        if not ph:
            continue
        cols, hdr, npg, cpp = MB.palette_colours(
            lgp.split_sections(V.decompressed(V.index[name]))[3])
        prgbs = [MA.palette_rgb(cols[p]) for p in range(npg)]
        cache = {}

        def art(page, pal):
            for p in (pal, 0):
                key = (name, page, p)
                nm = pick.get(key)
                if nm is None:
                    continue
                if key not in cache:
                    try:
                        rgba, w, h = dds_decode.decode_dds(reader.read(nm))
                        cache[key] = np.frombuffer(
                            rgba, np.uint8).reshape(h, w, 4)[..., :3].copy()
                    except Exception:                       # noqa: BLE001
                        cache[key] = None
                if cache[key] is not None:
                    return cache[key]
            return None

        # score every cell against every palette once
        scored = []                       # (slot, pal_named, err[npg], n[npg])
        for slot, sx, sy, pal in ph:
            if pal >= npg:
                continue
            img = art(slot, pal)
            if img is None:
                continue
            k = img.shape[1] // 256
            if k < 1:
                continue
            blk = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
            if blk.shape[:2] != (TILE * k, TILE * k):
                continue
            small = blk.reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3))
            if small.max() <= EMPTY_MAX:
                continue
            small = small.astype(np.uint8)
            e = np.empty(npg)
            n = np.empty(npg, int)
            for p in range(npg):
                ix = MA.quantise(small, prgbs[p])
                e[p] = np.abs(prgbs[p][ix].astype(np.int16)
                              - small.astype(np.int16)).mean()
                n[p] = np.unique(ix).size
            scored.append((slot, pal, e, n))
        cache.clear()
        if not scored:
            continue
        nfields += 1
        nscored += len(scored)

        # per-page choice
        bypage = defaultdict(list)
        for i, (slot, pal, e, n) in enumerate(scored):
            bypage[slot].append(i)
        page_pal = {}
        for slot, ids in bypage.items():
            tote = np.sum([scored[i][2] for i in ids], axis=0)
            page_pal[slot] = int(np.argmin(tote))
        adj = pic.most_common(1)[0][0] if pic else 0
        adj = min(adj, npg - 1)

        d_named = []
        for slot, pal, e, n in scored:
            for tag, p in (('named', pal), ('percell', int(np.argmin(e))),
                           ('perpage', page_pal[slot]), ('adjacent', adj)):
                tot[tag]['err'].append(e[p])
                tot[tag]['n'].append(n[p])
                tot[tag]['ref'] += e[p] > MA.MAX_QUANT_ERR
                tot[tag]['one'] += n[p] == 1
            d_named.append(e[pal] - e[page_pal[slot]])
        worst.append((float(np.mean(d_named)), name, len(scored),
                      float(np.mean([e[pal] for _s, pal, e, _n in scored])),
                      float(np.mean([e[page_pal[s]]
                                     for s, _p, e, _n in scored])),
                      dict(Counter(page_pal.values()))))
        if nfields >= a.limit:
            break

    print('fields %d   cells scored %d' % (nfields, nscored))
    print()
    print('%-10s %10s %10s %10s %10s'
          % ('choice', 'mean err', 'mean idx', 'flat(n=1)', 'REFUSED'))
    for t in TAGS:
        d = tot[t]
        print('%-10s %10.2f %10.2f %10d %10d'
              % (t, np.mean(d['err']), np.mean(d['n']), d['one'], d['ref']))
    print()
    print('the 15 fields perpage helps most:')
    print('%-10s %6s %9s %9s  %s'
          % ('field', 'cells', 'named', 'perpage', 'page->palette'))
    for d, name, n, en, ep, pp in sorted(worst, reverse=True)[:15]:
        print('%-10s %6d %9.1f %9.1f  %s' % (name, n, en, ep, pp))
    return 0


if __name__ == '__main__':
    sys.exit(main())
