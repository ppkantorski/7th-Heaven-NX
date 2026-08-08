#!/usr/bin/env python3
"""
diag_black.py -- find the black squares in a BUILT flevel.lgp.

This does not guess. It reads the flevel the packer actually wrote and, for
every tile that points at a truecolor page, looks at the cell that tile will
sample. A cell that is entirely 0x0000 is a black square on screen, because
field_convert_type2_layers (x86 0x63F385) turns every 0 pixel on a depth-2
page into opaque black.

So the output is the list of black squares the player will see, by field, by
page, by cell -- and which of four causes each one has:

  UNFILLED   the cell is inside a truecolor page but the packer never wrote
             it. Means a tile was pointed at a cell that was not allocated.
  EMPTY-ART  the cell was written, but written entirely transparent. Means
             the opacity gate let a transparent cell through.
  OOR        the tile's UV is outside the 16x16 grid.
  UNALIGNED  the tile's UV is not a multiple of 1/16, so it straddles cells.

It also checks the one structural limit that would drop tiles instead of
blacking them: `add_page_tile` (x86 0x6464BA) appends to a per-page list of
stride 0x1804 with 24-byte records, so a page holds at most 256 tiles PER
FRAME, and there is no bounds check. Layer 1 is not culled by screen position
(FFNx field/background.cpp: layer 1 adds every tile, layers 3 and 4 test the
viewport first), so a layer-1 page over 256 loses tiles or corrupts the next
page's list.

Usage:
    python3 diag_black.py <built flevel.lgp>
    python3 diag_black.py <built flevel.lgp> --field nmkin_1
    python3 diag_black.py <built flevel.lgp> --top 25
"""
import argparse
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lgp                          # noqa: E402
import field_bg_native as FN        # noqa: E402
import field_bg_repack as RP        # noqa: E402

STEP = RP.UV_SCALE // 16            # 625000


def cell_is_empty(page, cx, cy):
    """True if this cell of a truecolor page is entirely 0x0000."""
    side = page.px // 16
    sw = page.px * 2
    base = (cy * side) * sw + cx * side * 2
    d = page.data
    for y in range(side):
        row = d[base + y * sw:base + y * sw + side * 2]
        for i in range(0, len(row), 2):
            if row[i] or row[i + 1]:
                return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('--field')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--px', type=int, default=512)
    a = ap.parse_args()

    arc = lgp.Archive(a.flevel)
    per_field = collections.Counter()
    causes = collections.Counter()
    examples = []
    n_fields = n_tiles_d2 = 0
    pages_with_unfilled = 0
    cell_cache = {}
    over256 = []

    for name in sorted(arc.index):
        e = arc.index[name]
        if not arc.is_field(e):
            continue
        if a.field and name != a.field:
            continue
        try:
            s9 = lgp.split_sections(arc.decompressed(e))[8]
            pages, ts, _te = FN.parse_texture_block(s9, a.px)
            spans = FN._layer_tile_spans(s9, s9.find(b'BACK'), ts)
        except Exception:                                      # noqa: BLE001
            continue
        n_fields += 1
        pmap = {p.slot: p for p in pages if p is not None}
        # layer 1 is not culled, so its tiles all hit the 256-entry list
        n1 = struct.unpack_from('<H', s9, s9.find(b'BACK') + 8)[0]
        l1 = collections.Counter()
        for i, off in enumerate(spans):
            if i >= n1:
                break
            l1[s9[off + RP.T_TEXID]] += 1
        for slot, c in l1.items():
            if c > 256:
                over256.append((c, name, slot))
        cell_cache.clear()
        seen_cells = collections.defaultdict(set)
        bad_here = []
        for off in spans:
            slot = s9[off + RP.T_TEXID]
            p = pmap.get(slot)
            if p is None or p.depth != 2:
                continue
            n_tiles_d2 += 1
            u, v = struct.unpack_from('<II', s9, off + RP.T_SRC_X_BIG)
            if u % STEP or v % STEP:
                causes['UNALIGNED'] += 1
                bad_here.append((slot, -1, -1, 'UNALIGNED'))
                continue
            cx, cy = u // STEP, v // STEP
            if not (0 <= cx < 16 and 0 <= cy < 16):
                causes['OOR'] += 1
                bad_here.append((slot, cx, cy, 'OOR'))
                continue
            seen_cells[slot].add((cx, cy))
            key = (slot, cx, cy)
            empty = cell_cache.get(key)
            if empty is None:
                empty = cell_is_empty(p, cx, cy)
                cell_cache[key] = empty
            if empty:
                causes['EMPTY/UNFILLED'] += 1
                bad_here.append((slot, cx, cy, 'EMPTY/UNFILLED'))
        if bad_here:
            per_field[name] += len(bad_here)
            if len(examples) < 6:
                examples.append((name, bad_here[:8]))
        # how much of each truecolor page is dead weight
        for slot, p in pmap.items():
            if p.depth != 2:
                continue
            unused = 256 - len(seen_cells.get(slot, ()))
            if unused:
                pages_with_unfilled += 1

    if a.field:
        print('field %s' % a.field)
    print('fields scanned              %d' % n_fields)
    print('tiles pointing at truecolor %d' % n_tiles_d2)
    print('tiles that will draw BLACK  %d' % sum(causes.values()))
    for k, v in causes.most_common():
        print('    %-16s %d' % (k, v))
    print('truecolor pages with at least one unreferenced cell: %d'
          % pages_with_unfilled)
    print()
    print('LAYER-1 PAGES OVER THE 256-TILE LIST (add_page_tile has no bounds '
          'check): %d' % len(over256))
    over256.sort(reverse=True)
    for c, nm, slot in over256[:12]:
        print('   %-12s page %2d  %d layer-1 tiles  (limit 256)' % (nm, slot, c))
    print()
    if per_field:
        print('worst fields:')
        for nm, c in per_field.most_common(a.top):
            print('   %-12s %d black tile(s)' % (nm, c))
        print()
        print('examples (field, page, cell, cause):')
        for nm, rows in examples:
            for slot, cx, cy, why in rows:
                print('   %-12s page %2d  cell (%2d,%2d)  %s'
                      % (nm, slot, cx, cy, why))
    else:
        print('No tile samples an all-zero cell.')
        print('That means the black is NOT the packer writing empty pixels --')
        print('look at what else could stop a tile drawing (blend mode, the')
        print('per-page 256-tile list, or a layer being culled).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
