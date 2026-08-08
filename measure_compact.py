#!/usr/bin/env python3
"""
measure_compact.py -- how much of each field's background pages is DEAD?

    python3 measure_compact.py <flevel.lgp>

The question this answers is the one that decides whether the repack has to
grow the page count at all.

A background page is a 16x16 grid of cells and every present page costs one
texture. `field_load_textures` (x86 0x640292) abandons the loop on the first
texture it cannot allocate, so the page COUNT is the thing that breaks. The
repack currently ADDS pages, because a promoted page has to stay alive for
every tile that could not move.

But nothing says the surviving tiles have to stay on the page they are on.
Tiles are relocatable -- `src_x_big`/`src_y_big` (offsets 42/46) hold u,v as
u32 scaled by 1e7, and `texture_id` (offset 32) is one byte. That is the same
mechanism the truecolor repack already uses, and moving a cell between two
DEPTH-1 pages is strictly easier: same depth, same size, same 8-bit indices,
no colour conversion, and the tile's palette_ID keeps selecting the same
palette because palettes live per TILE, not per page.

So this measures, per field, without any model of the mod:

    occupied   distinct (page, cx, cy) cells any tile actually references
    unique     the same after collapsing cells whose 16x16 INDEX BLOCK is
               byte-identical -- flat sky, repeated masonry, and any two
               pages that share art
    floor      pages needed to hold `unique`, per blend group, at 256 cells
               a page

`floor` against the field's present page count is the headroom that exists
today, before a single truecolor page is added.

FX PAGES ARE COUNTED AS A CONSTRAINT, not ignored. A tile with an fx page
carries ONE u,v for both (FFNx field/background.cpp:199), so its main cell
and its fx cell must land at the SAME grid coordinate in two different
destination pages. `--fx-strict` reports the floor with that respected; the
default reports both.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                       # noqa: E402
import field_bg_native as FN                                     # noqa: E402
import field_bg_repack as RP                                     # noqa: E402

SECTION9 = 8


def _group(slot):
    for lo, hi, blend in FN.D1_GROUPS:
        if lo <= slot < hi:
            return blend
    for lo, hi, blend in FN.D2_GROUPS:
        if lo <= slot < hi:
            return ('d2', blend)
    return None


def cell_block(page, cx, cy, grid):
    """The raw index (or 565) bytes of one cell, as stored."""
    side = 256 // grid if page.depth == 1 else page.px // grid
    stride = (256 if page.depth == 1 else page.px) * page.depth
    w = side * page.depth
    d = page.data
    out = []
    for y in range(cy * side, (cy + 1) * side):
        b = y * stride + cx * side * page.depth
        out.append(d[b:b + w])
    return b''.join(out)


def one_field(sec9):
    pages, tex_start, tex_end = FN.parse_texture_block(sec9, FN.VANILLA_PX)
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    pmap = {p.slot: p for p in pages if p is not None}

    refs = set()                      # (slot, cx, cy) anything points at
    pairs = set()                     # (main_ref, fx_ref) that share a u,v
    for off in spans:
        slot = sec9[off + FN.TILE_TEXTURE_ID]
        p = pmap.get(slot)
        if p is None:
            continue
        grid = 8 if p.size_flag else 16
        u, v = struct.unpack_from('<II', sec9, off + RP.T_SRC_X_BIG)
        cx = int(round(u / RP.UV_SCALE * grid))
        cy = int(round(v / RP.UV_SCALE * grid))
        if not (0 <= cx < grid and 0 <= cy < grid):
            continue
        refs.add((slot, cx, cy))
        fx = sec9[off + RP.T_FX_PAGE]
        if fx and fx in pmap:
            refs.add((fx, cx, cy))
            pairs.add(((slot, cx, cy), (fx, cx, cy)))

    # collapse cells whose stored bytes are identical, within a blend group
    by_group = defaultdict(dict)      # group -> {bytes: first ref}
    ident = {}                        # ref -> canonical ref
    for ref in sorted(refs):
        slot, cx, cy = ref
        p = pmap[slot]
        g = _group(slot)
        grid = 8 if p.size_flag else 16
        try:
            blk = cell_block(p, cx, cy, grid)
        except Exception:
            blk = ('raw', ref)
        d = by_group[g]
        canon = d.setdefault((p.depth, p.size_flag, blk), ref)
        ident[ref] = canon

    occupied = len(refs)
    unique = sum(len(d) for d in by_group.values())
    floor = sum(-(-len(d) // 256) for d in by_group.values())
    # fx-strict: a paired main/fx cell must sit at one coordinate in two
    # pages, so the pair occupies a coordinate in BOTH -- charge the pair as
    # two cells that cannot be merged with anything else.
    paired = set()
    for a, b in pairs:
        paired.add(ident[a])
        paired.add(ident[b])
    strict_floor = sum(
        -(-(len(d) + sum(1 for r in d.values() if r in paired)) // 256)
        for g, d in by_group.items())
    return len(pmap), occupied, unique, floor, strict_floor


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--top', type=int, default=12)
    a = ap.parse_args(argv)

    arch = lgp.Archive(a.flevel)
    rows = []
    for e in arch.entries:
        if not arch.is_field(e):
            continue
        try:
            sec = lgp.split_sections(arch.decompressed(e))[SECTION9]
            rows.append((e['name'],) + one_field(sec))
        except Exception:
            continue
        if a.limit and len(rows) >= a.limit:
            break

    n = len(rows)
    print('fields measured           %d' % n)
    print('pages present, mean       %.2f      max %d (%s)'
          % (sum(r[1] for r in rows) / n,
             max(r[1] for r in rows),
             max(rows, key=lambda r: r[1])[0]))
    print('cells referenced, mean    %.1f      max %d'
          % (sum(r[2] for r in rows) / n, max(r[2] for r in rows)))
    print('cells after collapse      %.1f      max %d'
          % (sum(r[3] for r in rows) / n, max(r[3] for r in rows)))
    print('duplicate cells           %d of %d  (%.1f%%)'
          % (sum(r[2] - r[3] for r in rows), sum(r[2] for r in rows),
             100.0 * sum(r[2] - r[3] for r in rows) / sum(r[2] for r in rows)))
    print('pages needed (floor)      %.2f      max %d'
          % (sum(r[4] for r in rows) / n, max(r[4] for r in rows)))
    print('pages needed (fx-strict)  %.2f      max %d'
          % (sum(r[5] for r in rows) / n, max(r[5] for r in rows)))
    saved = [r for r in rows if r[5] < r[1]]
    print('fields that would shrink  %d of %d, total %d page(s) freed'
          % (len(saved), n, sum(r[1] - r[5] for r in saved)))
    print()
    print('%-12s %5s %7s %7s %6s %6s' % ('field', 'pages', 'cells',
                                         'unique', 'floor', 'strict'))
    for r in sorted(rows, key=lambda r: -(r[1] - r[5]))[:a.top]:
        print('%-12s %5d %7d %7d %6d %6d' % (r[0], r[1], r[2], r[3], r[4], r[5]))
    return rows


if __name__ == '__main__':
    main()
