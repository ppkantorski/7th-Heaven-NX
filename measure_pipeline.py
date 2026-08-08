#!/usr/bin/env python3
"""
measure_pipeline.py -- pages per field through the whole chain.

    python3 measure_pipeline.py <flevel.lgp> [--px 256] [--limit N]

Reports, per configuration, the page count each field ends with -- which is
the number that decides whether `field_load_textures` (x86 0x640292) runs out
of textures and leaves the rest of the picture black.

    vanilla   what the archive ships
    repack    after the truecolor promotion
    +compact  after packing the leftovers back down

The art provider is the same stub measure_dedup.py uses: Cosmos Limit Break's
measured coverage shape (one image for every page below slot 0x0F, one per
palette above it), every cell opaque. It models the PACKING, which is what
these settings change, without making the answer depend on which cells of the
mod happen to be transparent.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                       # noqa: E402
import field_bg_native as FN                                     # noqa: E402
import field_bg_repack as RP                                     # noqa: E402
import field_bg_compact as FC                                    # noqa: E402
from measure_dedup import StubArt, palettes_on_pages             # noqa: E402

SECTION9 = 8
PAL_BOUNDARY = 0x0F


def present(sec9, px):
    pages, _s, _e = FN.parse_texture_block(sec9, px)
    return sum(1 for p in pages if p is not None)


def run(path, px, limit=None, only=None, sl=(0, 1)):
    arch = lgp.Archive(path)
    rows = []
    promoted = []
    seen = -1
    for e in arch.entries:
        if not arch.is_field(e):
            continue
        seen += 1
        if seen % sl[1] != sl[0]:
            continue
        name = e['name']
        if only and name.lower() not in only:
            continue
        try:
            sec = lgp.split_sections(arch.decompressed(e))[SECTION9]
            pages, pal_on = palettes_on_pages(sec)
        except Exception:
            continue
        n_van = sum(1 for p in pages if p is not None)

        cache = {}

        def pals_for(slot, _p=pal_on):
            return {0} if slot < PAL_BOUNDARY else set(_p.get(slot, {0}))

        def art_for(slot, pal, _c=cache):
            k = (slot, pal)
            a = _c.get(k)
            if a is None:
                a = _c[k] = StubArt(px, k)
            return a

        try:
            out, st = RP.repack_section9(sec, name, art_for, page_px=px,
                                         pals_for=pals_for)
            n_rep = present(out, px)
        except Exception as exc:                                 # noqa: BLE001
            rows.append((name, n_van, n_van, n_van, 'ERR %r' % exc))
            continue
        try:
            out2, st2, cst = RP.repack_and_compact(
                sec, name, art_for, page_px=px, src_px=px, pals_for=pals_for)
            promoted.append((st2.pages_upgraded, st2.tiles))
            n_cmp = present(out2, px)
        except Exception as exc:                                 # noqa: BLE001
            rows.append((name, n_van, n_rep, n_rep, 'CERR %r' % exc))
            continue
        rows.append((name, n_van, n_rep, n_cmp, ''))
        if limit and len(rows) >= limit:
            break
    return rows, promoted


def report(rows, promoted=()):
    ok = [r for r in rows if not r[4]]
    if not ok:
        print('nothing measured')
        return
    n = len(ok)
    for i, label in ((1, 'vanilla'), (2, 'repack'), (3, '+compact')):
        col = [r[i] for r in ok]
        over = sum(1 for c in col if c > 12)
        grew = sum(1 for r in ok if r[i] > r[1])
        print('%-10s mean %5.2f   max %2d (%s)   over 12: %3d   grew: %3d'
              % (label, sum(col) / n, max(col),
                 max(ok, key=lambda r: r[i])[0], over, grew))
    print()
    print('%-12s %8s %7s %9s' % ('field', 'vanilla', 'repack', '+compact'))
    for r in sorted(ok, key=lambda r: -(r[2] - r[3]))[:15]:
        print('%-12s %8d %7d %9d' % (r[0], r[1], r[2], r[3]))
    if promoted:
        print()
        print('pages promoted   %d   tiles moved to truecolor %d'
              % (sum(a for a, _b in promoted), sum(b for _a, b in promoted)))
    errs = [r for r in rows if r[4]]
    if errs:
        print('\n%d error(s):' % len(errs))
        for r in errs[:5]:
            print('   %s %s' % (r[0], r[4]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('--px', type=int, default=256)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--fields', default=None)
    ap.add_argument('--slice', default=None)
    a = ap.parse_args(argv)
    only = set(x.strip().lower()
               for x in a.fields.split(',')) if a.fields else None
    sl = (0, 1)
    if a.slice:
        sl = tuple(int(x) for x in a.slice.split('/'))
    rows, promoted = run(a.flevel, a.px, a.limit, only, sl)
    report(rows, promoted)


if __name__ == '__main__':
    main()
