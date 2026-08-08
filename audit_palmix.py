#!/usr/bin/env python3
"""
audit_palmix.py -- HANDOFF-78 section 5.2b.

Count depth-1 pages carrying more than one palette, BEFORE and AFTER
`field_bg_compact.compact_section9`, over every field in an flevel archive.

WHY
===
`compact_section9` buckets cells by `(blend group, depth, size_flag, grid)`.
Palette is NOT in that key (field_bg_compact.py:403). For depth 2 that is
harmless -- colour is baked per pixel. For depth 1 it is the mechanism that
produced the Sector 6 yellow: a page carrying two palettes drawn through one
of them.

The margin freeze (field_bg_compact.py:332) protects only pages every one of
whose tiles is outside the 4:3 picture at ONE palette. Nothing protects an
interior paletted page. This measures whether that matters.

WHAT IT IS NOT
==============
This runs on the archive it is pointed at, in isolation. HANDOFF-78 section 2.9
is explicit that isolation has produced three regressions in this project. On
vanilla flevel it answers one question only, and answers it soundly:

    does the bucketing algorithm INCREASE palette mixing on depth-1 pages?

That is a property of the algorithm, and vanilla is a fair input for it. It
does NOT establish what the number is after `marginart` -> `marginpage` ->
`palkey` have run, because those change the page structure the packer sees.
Run it again on a modded flevel dump before believing any figure as the
pipeline's.
"""
from __future__ import annotations

import argparse
import collections
import struct
import sys

import field_bg_native as FN
import field_bg_compact as FC
import lgp


def palettes_per_page(sec9, src_px=FN.VANILLA_PX):
    """
    slot -> {palette: tile_count} for DEPTH-1 pages only, plus the same
    restricted to tiles that touch the 4:3 picture.

    Tile parsing mirrors compact_section9 exactly: texture_id at T_TEXID,
    palette at offset 22, dx as int16 at offset 2, and the same
    `-160 < dx + 16 and dx < 160` interior test the freeze uses, so the two
    passes cannot disagree about what a margin tile is.
    """
    pages, tex_start, tex_end = FN.parse_texture_block(sec9, src_px)
    pmap = {p.slot: p for p in pages if p is not None}
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    allpal = collections.defaultdict(collections.Counter)
    interior = collections.defaultdict(collections.Counter)
    for off in spans:
        slot = sec9[off + FC.T_TEXID]
        p = pmap.get(slot)
        if p is None or p.depth != 1:
            continue
        pal = sec9[off + 22]
        dx = struct.unpack_from('<h', sec9, off + 2)[0]
        allpal[slot][pal] += 1
        if -160 < dx + 16 and dx < 160:
            interior[slot][pal] += 1
    return allpal, interior


def mixed(d):
    return sum(1 for c in d.values() if len(c) > 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('archive')
    ap.add_argument('--px', type=int, default=FN.VANILLA_PX,
                    help='size depth-2 pages already have')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--worst', type=int, default=12)
    args = ap.parse_args()

    arc = lgp.Archive(args.archive)
    tot = dict(fields=0, skipped=0, failed=0,
               pages_before=0, pages_after=0,
               mix_before=0, mix_after=0,
               imix_before=0, imix_after=0,
               newly_mixed_pages=0, cells_moved=0, saved=0)
    offenders = []          # (field, delta_interior_mixed, before, after)
    names = sorted(arc.index)
    if args.limit:
        names = names[:args.limit]

    for nm in names:
        e = arc.index[nm]
        if not arc.is_field(e):
            continue
        try:
            raw = arc.decompressed(e)
            parts = lgp.split_sections(raw)
            sec9 = parts[8]
            a0, i0 = palettes_per_page(sec9, args.px)
        except Exception:                                      # noqa: BLE001
            # blackbgb / blackbgb.xone never parse as backgrounds. Expected.
            tot['skipped'] += 1
            continue
        try:
            new9, st = FC.compact_section9(sec9, src_px=args.px)
            a1, i1 = palettes_per_page(new9, args.px)
        except Exception as exc:                               # noqa: BLE001
            print(f'  ! {nm}: compaction failed -- {exc}', file=sys.stderr)
            tot['failed'] += 1
            continue
        tot['fields'] += 1
        tot['pages_before'] += len(a0)
        tot['pages_after'] += len(a1)
        tot['mix_before'] += mixed(a0)
        tot['mix_after'] += mixed(a1)
        tot['imix_before'] += mixed(i0)
        tot['imix_after'] += mixed(i1)
        tot['cells_moved'] += st.cells_moved
        tot['saved'] += max(0, st.saved)
        d = mixed(i1) - mixed(i0)
        if d > 0:
            tot['newly_mixed_pages'] += d
            offenders.append((nm, d, mixed(i0), mixed(i1)))

    print()
    print('  HANDOFF-78 5.2b -- palette mixing on DEPTH-1 pages across '
          'compact_section9')
    print(f'  archive {args.archive}   depth-2 page size {args.px}')
    print()
    print(f"  fields measured                      {tot['fields']:>8,}")
    print(f"  fields skipped (unparseable)         {tot['skipped']:>8,}")
    print(f"  fields where compaction errored      {tot['failed']:>8,}")
    print(f"  depth-1 pages   before / after       "
          f"{tot['pages_before']:>8,} / {tot['pages_after']:,}")
    print(f"  pages freed by compaction            {tot['saved']:>8,}")
    print(f"  cells relocated                      {tot['cells_moved']:>8,}")
    print()
    print('  MULTI-PALETTE DEPTH-1 PAGES  (the number 5.2b asks for)')
    print(f"    all tiles       before / after     "
          f"{tot['mix_before']:>8,} / {tot['mix_after']:,}"
          f"   {tot['mix_after'] - tot['mix_before']:+,}")
    print(f"    interior only   before / after     "
          f"{tot['imix_before']:>8,} / {tot['imix_after']:,}"
          f"   {tot['imix_after'] - tot['imix_before']:+,}")
    print()
    if tot['newly_mixed_pages']:
        print(f"  VERDICT: compaction ADDS {tot['newly_mixed_pages']:,} "
              f"multi-palette interior page(s) across "
              f"{len(offenders):,} field(s).")
        print('  Per HANDOFF-78 2.11 this is the live bug and the bucket key '
              'needs the palette in it.')
        print()
        print(f'  worst {args.worst} fields (interior mixed pages, '
              f'before -> after):')
        for nm, d, b, a in sorted(offenders, key=lambda r: -r[1])[:args.worst]:
            print(f'    {nm:<12} {b:>4} -> {a:<4}  {d:+}')
    else:
        print('  VERDICT: compaction adds NO multi-palette interior pages on '
              'this input.')
        print('  2.11 is not disproved -- see the module docstring on '
              'isolation. Re-run on a modded dump.')
    print()


if __name__ == '__main__':
    main()
