#!/usr/bin/env python3
"""
verify_iro_art.py -- prove the .iro ArtProvider reads Cosmos's field art.

    python3 verify_iro_art.py "/path/to/CosmosLimitBreak.iro"

Optionally name fields to decode:

    python3 verify_iro_art.py "/path/to/mod.iro" mrkt1 mds6_3 gaia_1

This is the one path I cannot exercise offline: my tests read the extracted
directory, the build reads the .iro. It touches nothing and writes nothing.
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import field_bg_repack as FR


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    iro = argv[1]
    fields = [f.lower() for f in argv[2:]] or ['mrkt1', 'mds6_3', 'md1_1']
    if not os.path.exists(iro):
        print('! no such file: %s' % iro)
        return 1

    print('reading %s (%.1f MB)' % (iro, os.path.getsize(iro) / 1048576))
    t0 = time.time()
    art = FR.ArtProvider([(iro, None)], 256, print)
    print('  indexed in %.1fs' % (time.time() - t0))
    print('  fields with art : %d' % len(art.fields()))
    print('  (field,page,pal) : %d' % len(art.slots))
    print('  ambiguous slots  : %d (base %d, arbitrary %d)'
          % (art.ambiguous, art.ambiguous_base, art.ambiguous_arbitrary))
    if not art:
        print('! the provider is EMPTY -- the build would promote nothing.')
        return 1

    print()
    ok = bad = 0
    for f in fields:
        if f not in art.fields():
            print('  %-10s no art in this .iro' % f)
            continue
        art_for = art.open(f)
        pages = sorted({k[1] for k in art.slots if k[0] == f})
        got = []
        for pg in pages[:4]:
            pals = sorted(art.palettes(pg))
            for q in pals[:2]:
                t = time.time()
                a = art_for(pg, q)
                if a is None:
                    bad += 1
                    got.append('page %d pal %d DECODE FAILED' % (pg, q))
                    continue
                ok += 1
                nz = sum(1 for i in range(0, min(len(a.buf), 2048), 2)
                         if a.buf[i] or a.buf[i + 1])
                got.append('page %d pal %d ok %dpx %.2fs %s'
                           % (pg, q, a.px, time.time() - t,
                              'HAS PIXELS' if nz else '*** ALL ZERO ***'))
        art.close()
        print('  %-10s pages %s' % (f, pages))
        for g in got:
            print('             %s' % g)

    print()
    print('decoded ok %d, failed %d' % (ok, bad))
    if bad or not ok:
        print('! the .iro reader is the problem -- send me this output.')
        return 1
    print('the .iro reader works; art reaches the build.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
