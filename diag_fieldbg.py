#!/usr/bin/env python3
"""
diag_fieldbg.py -- what the mod actually covers, in about ten seconds.

Reads the .iro's DIRECTORY only (no extraction, no decode) and the vanilla
flevel.lgp, and answers the two questions a 40-minute build otherwise answers
the slow way:

  * are the .dds named the way field_bg_repack thinks they are?
  * how much of the game will actually get upscaled, and how much of it will
    be borrowing a palette it does not own?

Usage:
    python3 diag_fieldbg.py mods/CosmosLimitBreak.iro <vanilla flevel.lgp>
    python3 diag_fieldbg.py mods/CosmosLimitBreak.iro flevel.lgp --field md1stin
"""
import argparse
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import iro                          # noqa: E402
import lgp                          # noqa: E402
import field_bg_native as FN        # noqa: E402
import field_bg_repack as RP        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('iro')
    ap.add_argument('flevel')
    ap.add_argument('--field', help='dump one field in detail')
    ap.add_argument('--samples', type=int, default=12)
    a = ap.parse_args()

    print('== reading the .iro directory (no extraction)')
    entries = iro.list_entries(a.iro)
    dds = [e for e in entries
           if e.lower().endswith('.dds')
           and '/field/' in e.lower().replace('\\', '/')]
    print('   %d entries, %d of them field .dds' % (len(entries), len(dds)))
    print('   sample names as stored:')
    for e in sorted(dds)[:a.samples]:
        print('      %s' % e)

    idx = RP.index_field_dds(entries)
    parsed = sum(len(v) for v in idx.values())
    print('   PARSED %d of %d as <field>_<page>_<palette>[_<hash>]'
          % (parsed, len(dds)))
    if parsed < len(dds):
        print('   !! %d file(s) did NOT parse -- the naming is not what '
              'field_bg_repack expects, and that is the bug. A few of them:'
              % (len(dds) - parsed))
        got = {n for v in idx.values() for n in v}
        for e in sorted(set(dds) - got)[:8]:
            print('      %s' % e)
    slots = RP.resolve(idx)
    amb = sum(1 for v in idx.values() if len(v) > 1)
    print('   %d slot(s), %d of them with more than one candidate'
          % (len(slots), amb))
    pg = collections.Counter(k[1] for k in slots)
    pl = collections.Counter(k[2] for k in slots)
    print('   page numbers seen : %s%s'
          % (sorted(pg)[:14], ' ...' if len(pg) > 14 else ''))
    print('   palette numbers   : %s%s'
          % (sorted(pl)[:14], ' ...' if len(pl) > 14 else ''))
    print('   (page should reach into the 20s-40s, palette should stay low;')
    print('    if they look swapped, the parse is inverted)')

    print()
    print('== against %s' % a.flevel)
    arc = lgp.Archive(a.flevel)
    tot = full = part = none_ = sizeflag = 0
    page_have = 0                    # mod ships SOME palette for this page
    cells = borrowed = 0
    worst = []
    palcmp = []
    by_page = collections.defaultdict(set)
    for (f, pg, q) in slots:
        by_page[(f, pg)].add(q)
    for name in sorted(arc.index):
        e = arc.index[name]
        if not arc.is_field(e):
            continue
        if a.field and name != a.field:
            continue
        try:
            s9 = lgp.split_sections(arc.decompressed(e))[8]
            pages, ts, _te = FN.parse_texture_block(s9)
            spans = FN._layer_tile_spans(s9, s9.find(b'BACK'), ts)
        except Exception:                                      # noqa: BLE001
            continue
        pmap = {p.slot: p for p in pages if p}
        pal = collections.defaultdict(set)
        cellset = collections.defaultdict(set)
        for off in spans:
            p = pmap.get(s9[off + RP.T_TEXID])
            if p is None or p.depth != 1:
                continue
            if p.size_flag:
                continue
            u, v = struct.unpack_from('<II', s9, off + RP.T_SRC_X_BIG)
            q = s9[off + RP.T_PALETTE]
            pal[p.slot].add(q)
            cellset[p.slot].add((u // 625000, v // 625000, q))
        sizeflag += sum(1 for p in pages
                        if p and p.depth == 1 and p.size_flag)
        f_missing = []
        for slot, pals in sorted(pal.items()):
            tot += 1
            mod_pals = by_page.get((name.lower(), slot), set())
            if mod_pals:
                page_have += 1
                if len(palcmp) < 10 and mod_pals != pals:
                    palcmp.append((name, slot, sorted(pals),
                                   sorted(mod_pals)))
            have = {q for q in pals if (name.lower(), slot, q) in slots}
            if have == pals:
                full += 1
            elif have:
                part += 1
            else:
                none_ += 1
            for _cx, _cy, q in cellset[slot]:
                cells += 1
                if q not in have:
                    borrowed += 1
            if pals - have:
                f_missing.append((slot, sorted(pals), sorted(have)))
        if a.field:
            print('   field %s' % name)
            for slot, pals, have in sorted(pal and f_missing or []):
                print('      page %2d  drawn with %s, mod has %s'
                      % (slot, pals, have or 'nothing'))
            for slot in sorted(pal):
                print('      page %2d  %d cell(s), palettes %s'
                      % (slot, len(cellset[slot]), sorted(pal[slot])))
        elif f_missing:
            worst.append((len(f_missing), name))

    if a.field:
        return 0
    print('   paletted pages drawn by tiles : %d' % tot)
    print('     mod ships art for the PAGE  : %d  (%.1f%%)   <-- the ceiling'
          % (page_have, 100.0 * page_have / max(tot, 1)))
    print('     no art for the page at all  : %d  (%.1f%%)  -> stays 256px'
          % (tot - page_have, 100.0 * (tot - page_have) / max(tot, 1)))
    print('     size-flag pages skipped     : %d  (separate from the above)'
          % sizeflag)
    print()
    print('   -- the three numbers below are the OLD palette-INTERSECTION')
    print('      rule, kept only to show why it was wrong. The packer does')
    print('      not use them.')
    print('        tile palette IDs all matched : %d' % full)
    print('        some matched                 : %d' % part)
    print('        none matched                 : %d  <- but the mod HAS the'
          ' art for most of these' % none_)
    if palcmp:
        print()
        print('   pages where the mod HAS art but under different palette '
              'IDs (this is what the fallback covers):')
        for nm, slot, a_, b_ in palcmp:
            print('      %-10s page %2d  flevel uses %s, mod dumped %s'
                  % (nm, slot, a_, b_))
        print('   `mod dumped [0]` on pages below 15 is CORRECT and'
              ' expected -- the engine')
        print('   makes one texture per page there, so one image is all there'
              ' is to dump.')
    print()
    print('   WHAT THE PACKER WILL ACTUALLY DO')
    print('     upgrade %d page(s) -- every page the mod ships art for.'
          % page_have)
    print('     A page is matched by PAGE, not by palette ID: below slot 0x0F')
    print('     the engine makes one texture per page and the tile palette_ID')
    print('     selects nothing, which is why the mod dumped only _00 there.')
    print('   Estimated flevel TEXTURE growth: %.2f GB'
          % (page_have * 524288 * 1.0 / 1024 ** 3))
    print('   (STRICT mode would upgrade %d.)' % full)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
