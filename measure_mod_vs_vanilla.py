#!/usr/bin/env python3
"""
measure_mod_vs_vanilla.py -- what does the mod's OWN section 9 cost, before
anything of ours touches it?

    python3 measure_mod_vs_vanilla.py <flevel.lgp> <CosmosLimitBreak.iro>

This is the measurement that should have come first. Every page budget, page
ceiling and promotion rule in this tree was designed against VANILLA page
counts. But the build splices `LIMIT BREAK\\flevel.lgp\\<field>.chunk.9` in
first -- 683 of them -- and only then promotes. If those sections already
carry more pages and more tiles than vanilla, then the ceiling was calibrated
against the wrong archive and every "no field exceeds 12" claim was measured
against a baseline the game never sees.

It also measures the ART SPAN, because that is where the widescreen art lives:
16:9 needs 427 tile units against 4:3's 320, and if the mod pays for the wider
picture with extra pages then the widescreen extension is exactly the part
that disappears when `field_load_textures` (x86 0x640292) runs out -- it
abandons the loop on the first failure, so the LAST pages are the ones that
never draw.
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

SECTION9 = 8
TILE_DST_X = 0                # s16
TILE_DST_Y = 2                # s16


def stats(sec9):
    pages, tex_start, _e = FN.parse_texture_block(sec9, FN.VANILLA_PX)
    live = [p for p in pages if p is not None]
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    xs = []
    for off in spans:
        x, = struct.unpack_from('<h', sec9, off + TILE_DST_X)
        xs.append(x)
    span = (max(xs) - min(xs) + 16) if xs else 0
    bytes_ = sum(RP._page_bytes(FN.VANILLA_PX, p.depth) for p in live)
    return len(live), len(spans), span, bytes_


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('iro')
    a = ap.parse_args(argv)

    import audit_real
    mod = audit_real.mod_sections(a.iro)

    arch = lgp.Archive(a.flevel)
    rows = []
    for e in arch.entries:
        if not arch.is_field(e):
            continue
        name = e['name'].lower()
        if name not in mod:
            continue
        try:
            van = lgp.split_sections(arch.decompressed(e))[SECTION9]
            v = stats(van)
            m = stats(mod[name])
        except Exception:                                        # noqa: BLE001
            continue
        rows.append((name,) + v + m)

    n = len(rows)
    if not n:
        print('nothing compared')
        return
    MB = 1048576.0
    print('fields with a mod section 9   %d' % n)
    print()
    print('%-22s %10s %10s' % ('', 'vanilla', 'mod'))
    print('%-22s %10.2f %10.2f' % ('pages, mean',
                                   sum(r[1] for r in rows) / n,
                                   sum(r[5] for r in rows) / n))
    print('%-22s %10d %10d' % ('pages, max',
                               max(r[1] for r in rows),
                               max(r[5] for r in rows)))
    print('%-22s %10.0f %10.0f' % ('tiles, mean',
                                   sum(r[2] for r in rows) / n,
                                   sum(r[6] for r in rows) / n))
    print('%-22s %10.0f %10.0f' % ('art span, mean',
                                   sum(r[3] for r in rows) / n,
                                   sum(r[7] for r in rows) / n))
    print('%-22s %10.2f %10.2f' % ('MB per field, mean',
                                   sum(r[4] for r in rows) / n / MB,
                                   sum(r[8] for r in rows) / n / MB))
    grew = [r for r in rows if r[5] > r[1]]
    wider = [r for r in rows if r[7] > r[3] + 8]
    print()
    print('fields where the MOD alone adds pages   %d  (+%d total)'
          % (len(grew), sum(r[5] - r[1] for r in grew)))
    print('fields where the MOD widens the art     %d' % len(wider))
    print('mod fields over 12 pages                %d'
          % sum(1 for r in rows if r[5] > 12))
    print('vanilla fields over 12 pages            %d'
          % sum(1 for r in rows if r[1] > 12))
    print()
    print('%-12s %6s %6s   %7s %7s   %6s %6s' %
          ('field', 'v.pg', 'm.pg', 'v.span', 'm.span', 'v.til', 'm.til'))
    for r in sorted(rows, key=lambda r: -(r[5] - r[1]))[:15]:
        print('%-12s %6d %6d   %7d %7d   %6d %6d'
              % (r[0], r[1], r[5], r[3], r[7], r[2], r[6]))
    return rows


if __name__ == '__main__':
    main()
