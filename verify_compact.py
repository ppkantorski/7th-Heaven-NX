#!/usr/bin/env python3
"""
verify_compact.py -- prove the compaction changes no pixel.

    python3 verify_compact.py <flevel.lgp>
    python3 verify_compact.py <flevel.lgp> --fields fship_2,del3

The claim being tested is per TILE, which is the only level at which it
matters: for every tile in every field, the 16x16 (or 32x32) block of pixels
it samples after compaction must be BYTE-IDENTICAL to the block it sampled
before, and its palette_ID must be unchanged. If a tile draws from an fx page,
the same must hold for the fx block, AND the two must still agree on one u,v
-- which is the constraint that would show up on screen as an animated effect
landing on the wrong square.

This does not model the engine. It reads the tile record, resolves it against
the TEXTURE block, and compares bytes. A pass means the picture cannot have
changed, because the pixels the hardware is asked for are the same pixels.
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
import field_bg_compact as FC                                    # noqa: E402

SECTION9 = 8


def repacked(sec9, name, px):
    """The section as the truecolor repack leaves it -- the position where
    compaction actually runs, and the only one where the leftovers are
    sparse enough for it to matter."""
    import field_bg_repack as RP
    from measure_dedup import StubArt, palettes_on_pages
    _pages, pal_on = palettes_on_pages(sec9)
    cache = {}

    def pals_for(slot):
        return {0} if slot < 0x0F else set(pal_on.get(slot, {0}))

    def art_for(slot, pal):
        k = (slot, pal)
        if k not in cache:
            cache[k] = StubArt(px, k)
        return cache[k]

    out, _st = RP.repack_section9(sec9, name, art_for, page_px=px,
                                  pals_for=pals_for)
    return out


def tile_view(sec9, px=FN.VANILLA_PX):
    """
    [(palette, main_block, fx_block, uv)] in tile order.

    `main_block` is the actual pixel bytes the tile samples. That is the
    thing that has to be invariant.
    """
    pages, tex_start, _ = FN.parse_texture_block(sec9, px)
    pmap = {p.slot: p for p in pages if p is not None}
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    out = []
    for off in spans:
        slot = sec9[off + FC.T_TEXID]
        fx = sec9[off + FC.T_FX_PAGE]
        pal = sec9[off + FN.TILE_PALETTE_ID]
        u, v = struct.unpack_from('<II', sec9, off + FC.T_SRC_X_BIG)
        p = pmap.get(slot)
        if p is None:
            out.append((pal, ('missing', slot), None, (u, v)))
            continue
        grid = 8 if p.size_flag else 16
        cx = int(round(u / FC.UV_SCALE * grid))
        cy = int(round(v / FC.UV_SCALE * grid))
        if not (0 <= cx < grid and 0 <= cy < grid):
            out.append((pal, ('oob', slot, u, v), None, (u, v)))
            continue
        main = FC._cell_bytes(p, cx, cy, grid)
        fxb = None
        if fx and fx in pmap:
            q = pmap[fx]
            fgrid = 8 if q.size_flag else 16
            if 0 <= cx < fgrid and 0 <= cy < fgrid:
                fxb = FC._cell_bytes(q, cx, cy, fgrid)
            else:
                fxb = ('oob-fx', fx)
        elif fx:
            fxb = ('fx-absent', fx)
        out.append((pal, main, fxb, (u, v)))
    return out


def check(name, sec9, px=FN.VANILLA_PX):
    before = tile_view(sec9, px)
    out, st = FC.compact_section9(sec9, src_px=px)
    if out is sec9 or not st.saved:
        return ('same', st, None)
    after = tile_view(out, px)
    if len(before) != len(after):
        return ('FAIL', st, 'tile count %d -> %d' % (len(before), len(after)))
    for i, (a, b) in enumerate(zip(before, after)):
        if a[0] != b[0]:
            return ('FAIL', st, 'tile %d palette %r -> %r' % (i, a[0], b[0]))
        if a[1] != b[1]:
            return ('FAIL', st, 'tile %d main pixels differ' % i)
        if a[2] != b[2]:
            return ('FAIL', st, 'tile %d fx pixels differ' % i)
    # and the section must still parse and round-trip
    try:
        FN.parse_texture_block(out, px)
    except Exception as e:                                       # noqa: BLE001
        return ('FAIL', st, 'reparse: %s' % e)
    return ('ok', st, None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('--fields', default=None)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--after-repack', action='store_true',
                    help='verify compaction where it actually runs -- on the '
                         'section the truecolor repack has already rewritten')
    ap.add_argument('--px', type=int, default=FN.VANILLA_PX)
    ap.add_argument('--slice', default=None,
                    help='I/N -- verify only every Nth field, offset I. Four '
                         'slices in parallel is the whole archive in a '
                         'quarter of the time and the union is the same set.')
    a = ap.parse_args(argv)
    sl_i, sl_n = (0, 1)
    if a.slice:
        sl_i, sl_n = (int(x) for x in a.slice.split('/'))
    only = set(x.strip().lower()
               for x in a.fields.split(',')) if a.fields else None

    arch = lgp.Archive(a.flevel)
    n_ok = n_same = 0
    fails = []
    errs = []
    saved = 0
    before_tot = after_tot = 0
    worst = (0, '')
    rows = []
    seen = -1
    for e in arch.entries:
        if not arch.is_field(e):
            continue
        seen += 1
        if seen % sl_n != sl_i:
            continue
        if only and e['name'].lower() not in only:
            continue
        try:
            sec = lgp.split_sections(arch.decompressed(e))[SECTION9]
        except Exception:
            continue
        try:
            if a.after_repack:
                sec = repacked(sec, e['name'], a.px)
            verdict, st, why = check(e['name'], sec, a.px)
        except Exception as exc:                                 # noqa: BLE001
            errs.append((e['name'], repr(exc)))
            continue
        before_tot += st.pages_before
        after_tot += st.pages_after or st.pages_before
        if st.pages_before > worst[0]:
            worst = (st.pages_before, e['name'])
        rows.append((e['name'], st.pages_before,
                     st.pages_after or st.pages_before))
        if verdict == 'ok':
            n_ok += 1
            saved += st.saved
        elif verdict == 'same':
            n_same += 1
        else:
            fails.append((e['name'], why))
        if a.limit and (n_ok + n_same + len(fails)) >= a.limit:
            break

    n = n_ok + n_same + len(fails)
    print('fields               %d' % n)
    print('compacted, verified  %d   (%d page(s) freed)' % (n_ok, saved))
    print('unchanged            %d' % n_same)
    print('FAILED               %d' % len(fails))
    for f in fails[:10]:
        print('    %s: %s' % f)
    if errs:
        print('errors               %d' % len(errs))
        for f in errs[:5]:
            print('    %s: %s' % f)
    if rows:
        print('pages per field      mean %.2f -> %.2f,  max %d -> %d'
              % (before_tot / len(rows), after_tot / len(rows),
                 max(r[1] for r in rows), max(r[2] for r in rows)))
        print()
        print('%-12s %6s %6s' % ('field', 'before', 'after'))
        for r in sorted(rows, key=lambda r: -(r[1] - r[2]))[:15]:
            print('%-12s %6d %6d' % r)
    return 1 if (fails or errs) else 0


if __name__ == '__main__':
    sys.exit(main())
