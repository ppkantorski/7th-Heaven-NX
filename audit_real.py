#!/usr/bin/env python3
"""
audit_real.py -- run the field-background chain over the REAL inputs.

    python3 audit_real.py <CosmosLimitBreak.iro> [--slice 0/4] [--px 256]

Every other verifier in this tree runs on VANILLA section 9s and a stub art
provider. The build does not. It splices the mod's own
`LIMIT BREAK\\flevel.lgp\\<field>.chunk.9` in first -- 683 of them -- and then
promotes from the mod's real .dds. Those section 9s have different page
layouts from vanilla (different slots, different size_flags, different page
counts), and the art has real transparency and real palette coverage.

So this is the only test that exercises what actually shipped. It reports:

  structural   a tile naming an ABSENT page, or a u,v off its grid -- the
               null-handle failures, which are what a black field or a crash
               look like
  pixels       every tile must sample byte-identical pixels across the
               compaction step
  pages        before -> after, and whether the no-growth promise held
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import iro                                                       # noqa: E402
import field_bg_native as FN                                     # noqa: E402
import field_bg_repack as RP                                     # noqa: E402
import field_bg_compact as FC                                    # noqa: E402
import verify_compact as VC                                      # noqa: E402


def mod_sections(iro_path):
    """{field: raw section 9 bytes} from the mod's own chunk.9 entries."""
    names = [n for n in iro.list_entries(iro_path)
             if n.lower().replace('\\', '/').endswith('.chunk.9')]
    rd = RP.IroReader(iro_path)
    out = {}
    with rd:
        for n in names:
            field = n.replace('\\', '/').rsplit('/', 1)[-1]
            field = field[:-len('.chunk.9')].lower()
            data = rd.read(n)
            if data:
                out[field] = data
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('iro')
    ap.add_argument('--px', type=int, default=256)
    ap.add_argument('--slice', default=None)
    ap.add_argument('--vanilla', default=None)
    ap.add_argument('--limit', type=int, default=None)
    a = ap.parse_args(argv)
    sl_i, sl_n = (0, 1)
    if a.slice:
        sl_i, sl_n = (int(x) for x in a.slice.split('/'))

    secs = mod_sections(a.iro)
    art = RP.ArtProvider([(a.iro, None)], a.px, lambda *x: None)
    # vanilla page count per field -- the real ceiling, see repack_and_compact
    van = {}
    if a.vanilla:
        import lgp
        arch = lgp.Archive(a.vanilla)
        for e in arch.entries:
            if not arch.is_field(e):
                continue
            try:
                s9 = lgp.split_sections(arch.decompressed(e))[8]
                van[e['name'].lower()] = len(
                    [p for p in FN.parse_texture_block(s9, FN.VANILLA_PX)[0]
                     if p is not None])
            except Exception:
                pass

    n = 0
    struct_bad = []
    pixel_bad = []
    grew = []
    parse_bad = []
    before_tot = after_tot = 0
    worst = (0, '')
    for i, field in enumerate(sorted(secs)):
        if i % sl_n != sl_i:
            continue
        if a.limit and n >= a.limit:
            break
        sec = secs[field]
        try:
            n_before = len([p for p in
                            FN.parse_texture_block(sec, FN.VANILLA_PX)[0]
                            if p is not None])
        except Exception as exc:                                 # noqa: BLE001
            parse_bad.append((field, 'input: %r' % exc))
            continue
        n += 1
        try:
            new9, _k = FN.resize_section9(sec, a.px)
        except Exception as exc:                                 # noqa: BLE001
            parse_bad.append((field, 'resize: %r' % exc))
            continue

        # --- promotion + compaction, exactly as build.py orders it
        try:
            if field in art.fields():
                af = art.open(field)
                try:
                    out, _st, _cst = RP.repack_and_compact(
                        new9, field, af, a.px, src_px=a.px,
                        pals_for=art.palettes,
                        vanilla_pages=van.get(field))
                finally:
                    art.close()
            else:
                out, _cst = FC.compact_section9(new9, src_px=a.px)
        except Exception as exc:                                 # noqa: BLE001
            struct_bad.append((field, 'EXCEPTION %r' % exc))
            continue

        # --- structural: the null-handle failures
        why = FC.self_check(new9, out, a.px)
        if why is not None:
            struct_bad.append((field, why))
            continue

        # --- pixels: compaction alone must be invisible
        try:
            b = VC.tile_view(new9, a.px)
            c = VC.tile_view(out, a.px)
            if len(b) == len(c):
                for j, (x, y) in enumerate(zip(b, c)):
                    if x[0] != y[0]:
                        pixel_bad.append((field, 'tile %d palette' % j))
                        break
        except Exception as exc:                                 # noqa: BLE001
            pixel_bad.append((field, 'view: %r' % exc))

        try:
            n_after = len([p for p in FN.parse_texture_block(out, a.px)[0]
                           if p is not None])
        except Exception as exc:                                 # noqa: BLE001
            parse_bad.append((field, 'output: %r' % exc))
            continue
        before_tot += n_before
        after_tot += n_after
        if n_after > worst[0]:
            worst = (n_after, field)
        tgt = van.get(field, n_before)
        if n_after > tgt:
            grew.append((field, tgt, n_after))

    print('fields (mod chunk.9)   %d' % n)
    print('STRUCTURAL failures    %d' % len(struct_bad))
    for f in struct_bad[:15]:
        print('    %-12s %s' % f)
    print('pixel failures         %d' % len(pixel_bad))
    for f in pixel_bad[:8]:
        print('    %-12s %s' % f)
    print('parse problems         %d' % len(parse_bad))
    for f in parse_bad[:8]:
        print('    %-12s %s' % f)
    print('OVER vanilla count     %d' % len(grew))
    for f in sorted(grew, key=lambda r: -(r[2] - r[1]))[:10]:
        print('    %-12s %d -> %d' % f)
    if n:
        print('pages mean %.2f -> %.2f,  worst after: %d (%s)'
              % (before_tot / n, after_tot / n, worst[0], worst[1]))
    return 1 if (struct_bad or pixel_bad) else 0


if __name__ == '__main__':
    sys.exit(main())
