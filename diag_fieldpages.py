#!/usr/bin/env python3
"""
diag_fieldpages.py -- how much background texture each field actually asks for.

    python3 diag_fieldpages.py <flevel.lgp>
    python3 diag_fieldpages.py <flevel.lgp> --over 5.0
    python3 diag_fieldpages.py <flevel.lgp> nmkin_1 nmkin_2 nmkin_3
    python3 diag_fieldpages.py <flevel.lgp> --csv pages.csv

WHY
===
`field_load_textures` (x86 `0x640292`) **aborts the whole loop on the first
page it cannot allocate**, and every page after that one keeps handle 0 and
never draws. Its tiles are silently skipped and you see whatever the buffer
already held -- black, green, tan, whatever happens to be there.

So a field is either entirely fine or badly broken, and which it is depends
on one number: the total texture that field asks the console for. That is
what this measures, per field, out of the archive you actually built.

`field_bg_repack.budget_bytes()` records where the line is, measured on
hardware against a real build:

    elmin1_1, elmin1_2, elmin2_1, elmin2_2   CLEAN   1 x 512   2.44 MB
    nmkin_1                                  BLACK   3 x 512   6.06 MB
    nmkin_2, nmkin_3, nmkin_4, mds5_1        BLACK   4 x 512   7.88-8.50 MB

Vanilla's heaviest field is 11 pages at 256px = 3.44 MB, which is the order
the port was provisioned for.

THE COST MODEL
==============
Straight from `field_bg_repack._page_bytes`, so this agrees with the build:

    page bytes = px*px*depth + px*px*4

the second term being the 32bpp surface the engine builds from the raw page
(x86 `0x63FAAB`). So:

    512px truecolor (depth 2)   1.50 MB each
    256px paletted  (depth 1)   0.31 MB each

**Raising the "Field background memory budget" does not give the console more
memory.** It tells the BUILD how greedy to be: a higher number promotes more
pages to 512px truecolor at 1.50 MB apiece, so a higher budget makes this
failure MORE likely, not less. It is a greed dial, not a permission slip.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lgp                                                      # noqa: E402
import diag_common as DC                                        # noqa: E402

SURFACE_BPP = 4
SECTION9 = 8

# Where hardware put the line. See field_bg_repack.budget_bytes().
MEASURED_CLEAN_MB = 2.44
MEASURED_BLACK_MB = 6.06


def page_bytes(px, depth):
    """field_bg_repack._page_bytes, repeated so this file stands alone."""
    return px * px * depth + px * px * SURFACE_BPP


def measure(path, only=None):
    arc = lgp.Archive(path)
    rows, skipped = [], []
    for key in sorted(arc.index):
        entry = arc.index[key]
        if not arc.is_field(entry):
            continue
        if only and entry['name'].lower() not in only:
            continue
        try:
            raw = arc.decompressed(entry)
            sec = lgp.split_sections(raw)[SECTION9]
            pages, _s, _e, page_px = DC.parse_pages(sec)
        except Exception as exc:                               # noqa: BLE001
            skipped.append((entry['name'], str(exc)[:50]))
            continue
        present = [p for p in pages if p is not None]
        if not present:
            continue
        total = sum(page_bytes(p.px, p.depth) for p in present)
        d2 = [p for p in present if p.depth == 2]
        d1 = [p for p in present if p.depth == 1]
        rows.append({'name': entry['name'],
                     'pages': len(present),
                     'truecolor': len(d2), 'paletted': len(d1),
                     'mb': total / 1048576.0,
                     'sizes': sorted({p.px for p in present})})
    return rows, skipped


def verdict(mb):
    if mb >= MEASURED_BLACK_MB:
        return 'OVER the value measured BLACK'
    if mb >= 5.0:
        return 'inside the measured grey zone'
    if mb >= MEASURED_CLEAN_MB:
        return 'above the largest measured-clean field'
    return 'below every measured failure'


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('flevel')
    ap.add_argument('fields', nargs='*')
    ap.add_argument('--over', type=float, default=None,
                    help='only list fields costing at least this many MB '
                         '(default: the measured-clean ceiling, %.2f)'
                         % MEASURED_CLEAN_MB)
    ap.add_argument('--top', type=int, default=30)
    ap.add_argument('--csv')
    a = ap.parse_args(argv)

    only = {f.lower() for f in a.fields} or None
    print('reading %s ...' % a.flevel)
    rows, skipped = measure(a.flevel, only)
    if not rows:
        print('! nothing measured')
        return 2
    rows.sort(key=lambda r: -r['mb'])

    print()
    print('page cost: 512px truecolor %.2f MB, 256px paletted %.2f MB'
          % (page_bytes(512, 2) / 1048576.0, page_bytes(256, 1) / 1048576.0))
    print('hardware:  %.2f MB measured CLEAN, %.2f MB measured BLACK'
          % (MEASURED_CLEAN_MB, MEASURED_BLACK_MB))
    print()

    if only:
        for r in rows:
            print('%-12s %5.2f MB   %d page(s): %d truecolor, %d paletted %s'
                  % (r['name'], r['mb'], r['pages'], r['truecolor'],
                     r['paletted'], r['sizes']))
            print('             %s' % verdict(r['mb']))
        return 0

    cut = a.over if a.over is not None else MEASURED_CLEAN_MB
    over = [r for r in rows if r['mb'] >= cut]
    black = [r for r in rows if r['mb'] >= MEASURED_BLACK_MB]
    print('%d field(s) measured' % len(rows))
    print('  %4d at or over %.2f MB  (the largest field measured CLEAN)'
          % (len([r for r in rows if r['mb'] >= MEASURED_CLEAN_MB]),
             MEASURED_CLEAN_MB))
    print('  %4d at or over %.2f MB  (measured BLACK on hardware)'
          % (len(black), MEASURED_BLACK_MB))
    print('  heaviest %.2f MB, median %.2f MB'
          % (rows[0]['mb'], rows[len(rows) // 2]['mb']))
    print()
    print('Heaviest fields -- these fail first, and when one does, every page')
    print('after the one that failed draws NOTHING for the whole field:')
    print()
    print('  %-12s %8s  %5s %5s %5s   %s'
          % ('field', 'MB', 'pages', 'true', 'pal', ''))
    for r in over[:a.top]:
        flag = '  <-- over the measured-black line' \
            if r['mb'] >= MEASURED_BLACK_MB else ''
        print('  %-12s %8.2f  %5d %5d %5d%s'
              % (r['name'], r['mb'], r['pages'], r['truecolor'],
                 r['paletted'], flag))

    if a.csv:
        import csv
        with open(a.csv, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['field', 'mb', 'pages', 'truecolor', 'paletted',
                        'verdict'])
            for r in rows:
                w.writerow(['%s' % r['name'], '%.3f' % r['mb'], r['pages'],
                            r['truecolor'], r['paletted'], verdict(r['mb'])])
        print()
        print('wrote %s' % a.csv)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
