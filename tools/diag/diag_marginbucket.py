#!/usr/bin/env python3
"""
diag_marginbucket.py -- HANDOFF-80 5.1 step 1, as a lookup instead of a guess.

For one field, classify EVERY layer-1 tile by what actually happened to its
source cell between the vanilla archive and the built one, keyed on the tile's
DESTINATION position so page relocation by the repack/compaction cannot confuse
it.

    python3 diag_marginbucket.py <built.lgp> <vanilla.lgp> md8_1 [-o map.png]

Buckets, in the order they are tested:

    FLAT_KEPT     flat in vanilla, still flat in built, SAME value
                  -> the pass declined this cell. This is the reported defect.
    FLAT_RECOL    flat in vanilla, still flat in built, DIFFERENT value
                  -> written, but the mod's art was flat too (the darkened
                     placeholder case) or the palette moved.
    FLAT_FILLED   flat in vanilla, has detail in built  -> the pass worked.
    ART_KEPT      had detail in vanilla, byte-identical block in built.
    ART_CHANGED   had detail in vanilla, different in built.
    NEW           no tile at that destination in vanilla.

Also reports, for each margin tile, WHY the pass could have declined it:
whether its page is a `mixed_pages` veto in ff7nx_marginart.fillable_cells,
and whether its cell is shared with a non-margin tile.

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

import diag_common as DC          # noqa: E402
import ff7nx_marginblack as MB    # noqa: E402
import lgp                        # noqa: E402
import render_field as RF         # noqa: E402

TILE = 16
SECTION9 = 8


def survey(raw):
    parts = lgp.split_sections(raw)
    surv = DC.survey(parts[SECTION9])
    pages = {p.slot: p for p in surv['pages']}
    arrays = {s: MB.page_array(p) for s, p in pages.items()}
    tiles = MB.read_tiles(parts[SECTION9], surv, pages)
    return parts, surv, pages, arrays, tiles


def mixed_pages_of(parts, surv, pages):
    """Reproduce ff7nx_marginart.fillable_cells' SKIP_MIXED_PALETTE_PAGES."""
    page_marg, page_all = {}, {}
    for t in MB.read_tiles(parts[SECTION9], surv, pages):
        p = pages.get(t.slot)
        if p is None or p.depth != 1:
            continue
        page_all.setdefault(t.slot, set()).add(t.pal)
        if t.layer == 1 and t.outside_43:
            page_marg.setdefault(t.slot, set()).add(t.pal)
    return {s: (m, page_all.get(s, m)) for s, m in page_marg.items()
            if page_all.get(s, m) != m}


def block_of(arrays, t):
    a = arrays.get(t.slot)
    if a is None:
        return None
    return MB.source_block(a[0], a[1], t.sx, t.sy)


def classify(field, built, vanilla):
    bp, bs, bpg, ba, bt = survey(built)
    vp, vs, vpg, va, vt = survey(vanilla)
    vmap = {(t.layer, t.dx, t.dy): t for t in vt}
    mixed = mixed_pages_of(bp, bs, bpg)

    # cells a non-margin tile also samples, in the BUILT archive
    shared = set()
    for t in bt:
        if not (t.layer == 1 and t.outside_43):
            shared.add(t.cell)

    rows = []
    for t in bt:
        if t.layer != 1 or t.depth != 1:
            continue
        b = block_of(ba, t)
        if b is None:
            continue
        v = vmap.get((t.layer, t.dx, t.dy))
        vb = block_of(va, v) if v is not None else None
        bu, vu = np.unique(b), (np.unique(vb) if vb is not None else None)
        if vb is None:
            bucket = 'NEW'
        elif vu.size == 1 and bu.size == 1:
            bucket = 'FLAT_KEPT' if bu[0] == vu[0] else 'FLAT_RECOL'
        elif vu.size == 1:
            bucket = 'FLAT_FILLED'
        elif np.array_equal(b, vb):
            bucket = 'ART_KEPT'
        else:
            bucket = 'ART_CHANGED'
        rows.append(dict(
            dx=t.dx, dy=t.dy, slot=t.slot, sx=t.sx, sy=t.sy, pal=t.pal,
            margin=bool(t.outside_43), bucket=bucket,
            nidx_built=int(bu.size), nidx_van=int(vu.size) if vu is not None
            else -1,
            mixed=t.slot in mixed, shared=t.cell in shared,
            vpal=v.pal if v is not None else -1,
        ))
    return rows, mixed, bp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('built')
    ap.add_argument('vanilla')
    ap.add_argument('field')
    ap.add_argument('-o', '--out', default='marginbucket.png')
    a = ap.parse_args(argv)

    A, B = lgp.Archive(a.built), lgp.Archive(a.vanilla)
    rows, mixed, bparts = classify(a.field, A.decompressed(A.index[a.field]),
                                   B.decompressed(B.index[a.field]))

    print(f'{a.field}: {len(rows)} layer-1 depth-1 tiles')
    print(f'  HALF_43 = {MB.HALF_43}')
    print()
    for scope, sel in (('MARGIN (outside 4:3)', True), ('INTERIOR', False)):
        c = Counter(r['bucket'] for r in rows if r['margin'] is sel)
        n = sum(c.values())
        print(f'  {scope}: {n} tiles')
        for k, v in c.most_common():
            print(f'      {k:<14} {v:6d}  {100.0 * v / max(n, 1):5.1f}%')
    print()
    print('  pages where margin and interior tiles name different palettes')
    print('  (ff7nx_marginart vetoes every margin cell on these):')
    if not mixed:
        print('      none')
    for s, (m, allp) in sorted(mixed.items()):
        ntile = sum(1 for r in rows if r['slot'] == s and r['margin'])
        print(f'      slot {s:3d}   margin pals {sorted(m)}   '
              f'all pals {sorted(allp)}   {ntile} margin layer-1 tiles')
    print()
    kept = [r for r in rows if r['margin'] and r['bucket'] == 'FLAT_KEPT']
    print(f'  FLAT_KEPT margin tiles: {len(kept)}')
    why = Counter()
    for r in kept:
        why[('mixed-page veto' if r['mixed'] else
             'shared with interior' if r['shared'] else
             'other')] += 1
    for k, v in why.most_common():
        print(f'      {k:<24} {v}')
    if kept:
        print('      slots: ', dict(Counter(r['slot'] for r in kept)))
        print('      dx range %d..%d   dy range %d..%d'
              % (min(r['dx'] for r in kept), max(r['dx'] for r in kept),
                 min(r['dy'] for r in kept), max(r['dy'] for r in kept)))

    # ------------------------------------------------------------ the map
    from PIL import Image, ImageDraw
    img, (x0, y0) = RF.render(A.decompressed(A.index[a.field]), (1, 2))
    H, W = img.shape[:2]
    COLOUR = {
        'FLAT_KEPT':   (255,   0,   0),
        'FLAT_RECOL':  (255, 160,   0),
        'FLAT_FILLED': (  0, 220,   0),
        'ART_KEPT':    ( 60,  60, 200),
        'ART_CHANGED': (  0, 200, 220),
        'NEW':         (255,   0, 255),
    }
    over = np.zeros((H, W, 3), np.uint8)
    for r in rows:
        dy, dx = r['dy'] - y0, r['dx'] - x0
        if not (0 <= dy < H and 0 <= dx < W):
            continue
        over[dy:dy + TILE, dx:dx + TILE] = COLOUR[r['bucket']]
    blend = (img.astype(np.int32) * 0.45 + over.astype(np.int32) * 0.55)
    blend = np.clip(blend, 0, 255).astype(np.uint8)
    PAD, LBL = 8, 16
    out = Image.new('RGB', (W * 2 + PAD * 3, H + LBL + PAD * 2), (20, 20, 24))
    d = ImageDraw.Draw(out)
    d.text((PAD, PAD), f'{a.field} built', fill=(230, 230, 235))
    d.text((PAD * 2 + W, PAD), '  '.join(f'{k}' for k in COLOUR),
           fill=(230, 230, 235))
    out.paste(Image.fromarray(img), (PAD, PAD + LBL))
    out.paste(Image.fromarray(blend), (PAD * 2 + W, PAD + LBL))
    out.save(a.out)
    print(f'\n  wrote {a.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
