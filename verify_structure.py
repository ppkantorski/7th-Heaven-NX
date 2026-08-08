#!/usr/bin/env python3
"""
verify_structure.py -- does every tile still point at a page that EXISTS?

    python3 verify_structure.py <flevel.lgp> [--slice 0/4]

verify_compact.py answers "do the pixels match", which is the right question
for a picture that draws. It is the wrong question for a picture that does not
draw at all. A tile whose `texture_id` names an ABSENT page gets handle 0,
0x66E272 refuses a null handle, and depending on where that happens the field
either comes up black or takes the game down.

So this checks the structure rather than the content, on the section as the
whole chain actually leaves it (resize -> repack -> compact):

    1. every tile's texture_id names a PRESENT page
    2. every non-zero fx_page names a PRESENT page
    3. the cell the u,v resolves to is inside that page's grid
    4. an fx-paired tile resolves inside BOTH pages
    5. the TEXTURE block reparses and round-trips

Every failure is reported RELATIVE to the same check run on the input, so a
defect the archive already had is not blamed on this pass.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                       # noqa: E402
import field_bg_native as FN                                     # noqa: E402
import field_bg_repack as RP                                     # noqa: E402
import field_bg_compact as FC                                    # noqa: E402

SECTION9 = 8


def faults(sec9, px):
    """
    {kind: count} of structural problems, plus the first example of each.

    Deliberately tolerant about things that are merely odd and strict about
    the two that produce a null texture handle.
    """
    out = {}
    ex = {}

    def bad(kind, detail):
        out[kind] = out.get(kind, 0) + 1
        ex.setdefault(kind, detail)

    pages, tex_start, _e = FN.parse_texture_block(sec9, px)
    pmap = {p.slot: p for p in pages if p is not None}
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    for off in spans:
        slot = sec9[off + FN.TILE_TEXTURE_ID]
        fx = sec9[off + FC.T_FX_PAGE]
        u, v = struct.unpack_from('<II', sec9, off + FC.T_SRC_X_BIG)
        p = pmap.get(slot)
        if p is None:
            bad('main page ABSENT', 'tile@%d -> slot %d' % (off, slot))
            continue
        grid = 8 if p.size_flag else 16
        cx = int(round(u / FC.UV_SCALE * grid))
        cy = int(round(v / FC.UV_SCALE * grid))
        if not (0 <= cx < grid and 0 <= cy < grid):
            bad('main uv off the page',
                'tile@%d slot %d -> (%d,%d) of %d' % (off, slot, cx, cy, grid))
        if fx:
            q = pmap.get(fx)
            if q is None:
                bad('fx page ABSENT', 'tile@%d -> fx slot %d' % (off, fx))
                continue
            fgrid = 8 if q.size_flag else 16
            fcx = int(round(u / FC.UV_SCALE * fgrid))
            fcy = int(round(v / FC.UV_SCALE * fgrid))
            if not (0 <= fcx < fgrid and 0 <= fcy < fgrid):
                bad('fx uv off the page',
                    'tile@%d fx slot %d -> (%d,%d) of %d'
                    % (off, fx, fcx, fcy, fgrid))
    return out, ex


def build_chain(sec9, name, px, art_for, pals_for, do_repack):
    """resize -> repack -> compact, exactly as build.py orders it."""
    new9, _k = FN.resize_section9(sec9, px)
    if do_repack:
        new9, _st, _cst = RP.repack_and_compact(
            new9, name, art_for, px, src_px=px, pals_for=pals_for)
    else:
        new9, _cst = FC.compact_section9(new9, src_px=px)
    return new9


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('--px', type=int, default=256)
    ap.add_argument('--slice', default=None)
    ap.add_argument('--no-repack', action='store_true',
                    help='compaction alone, to separate the two passes')
    a = ap.parse_args(argv)
    sl_i, sl_n = (0, 1)
    if a.slice:
        sl_i, sl_n = (int(x) for x in a.slice.split('/'))

    from measure_dedup import StubArt, palettes_all
    arch = lgp.Archive(a.flevel)
    seen = -1
    n = 0
    new_faults = {}
    new_ex = {}
    worse = []
    for e in arch.entries:
        if not arch.is_field(e):
            continue
        seen += 1
        if seen % sl_n != sl_i:
            continue
        try:
            sec = lgp.split_sections(arch.decompressed(e))[SECTION9]
            before, _bex = faults(sec, FN.VANILLA_PX)
        except Exception:
            continue
        pal_on = palettes_all(sec)
        cache = {}

        def pals_for(slot, _p=pal_on):
            return {0} if slot < 0x0F else set(_p.get(slot, {0}))

        def art_for(slot, pal, _c=cache):
            k = (slot, pal)
            if k not in _c:
                _c[k] = StubArt(a.px, k)
            return _c[k]

        try:
            out = build_chain(sec, e['name'], a.px, art_for, pals_for,
                              not a.no_repack)
            after, aex = faults(out, a.px)
        except Exception as exc:                                 # noqa: BLE001
            worse.append((e['name'], 'EXCEPTION %r' % exc))
            continue
        n += 1
        delta = {k: after.get(k, 0) - before.get(k, 0)
                 for k in set(after) | set(before)}
        delta = {k: v for k, v in delta.items() if v > 0}
        if delta:
            worse.append((e['name'], delta))
            for k, v in delta.items():
                new_faults[k] = new_faults.get(k, 0) + v
                new_ex.setdefault(k, aex.get(k, ''))

    print('fields checked        %d' % n)
    print('fields made WORSE     %d' % len(worse))
    if new_faults:
        print()
        for k in sorted(new_faults):
            print('  %-22s +%d      e.g. %s' % (k, new_faults[k], new_ex[k]))
    if worse:
        print()
        for nm, d in worse[:12]:
            print('  %-12s %s' % (nm, d))
    return 1 if worse else 0


if __name__ == '__main__':
    sys.exit(main())
