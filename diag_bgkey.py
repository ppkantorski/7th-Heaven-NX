#!/usr/bin/env python3
"""
diag_bgkey.py -- what colour is each field's palette entry 0?

    python3 diag_bgkey.py <flevel.lgp>                    # every field
    python3 diag_bgkey.py <flevel.lgp> md8_1 mkt_ia       # named fields
    python3 diag_bgkey.py <flevel.lgp> --predict          # THE HARDWARE CHECK
    python3 diag_bgkey.py <flevel.lgp> --dump ancnt1      # raw section layout
    python3 diag_bgkey.py <flevel.lgp> --csv bgkey.csv

RUN THIS BEFORE THE BUILD.
==========================
HANDOFF-61 claims the 16:9 margin is the field's own palette entry 0. That
claim is falsifiable ON THE BENCH, against screenshots that already exist,
and this is the tool that does it. No reboot, no card copy.

`--predict` prints the colour this module says each REPORTED field's margin
should be, next to what was reported from hardware:

    field      entry 0   predict  reported where
    mkt_ia     #C8A878   tan      tan      Wall Market
    md8_1      #00FF00   green    green    Sector 7 (MEASURED #00FF00)
    hbfront    #000000   black    black    Honey Bee Inn -- THE CONTROL

If they match, ship `ff7nx_bgkey` and one build fixes every field. If Wall
Market's entry 0 comes back black while its bars are tan, the theory is dead
and HANDOFF-60 §3.2 re-opens. Either answer is worth more than a boot.

`--dump` is the escape hatch: it prints every section's length and first
bytes for one field, which is what settles a parse failure in one round trip
instead of three. The first version of this tool read section 9 -- which
opens with the literal string "PALETTE" and is NOT the palette section --
and its self-check caught it on all 711 fields rather than writing garbage.
The palette is section 4, index 3.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lgp                                                      # noqa: E402
import ff7nx_bgkey as BK                                        # noqa: E402

# The hardware reports, verbatim from HANDOFF-60 §2 Group A. Kept as data so
# the comparison is mechanical rather than remembered.
REPORTED = [
    ('mkt_ia',   'tan',    'Wall Market'),
    ('mkt_m',    'tan',    'Wall Market'),
    ('mkt_s1',   'tan',    'Wall Market'),
    ('mkt_w',    'tan',    'Wall Market'),
    ('md8_1',    'green',  'Sector 7, near the tower (MEASURED #00FF00)'),
    ('md8_2',    'green',  'Sector 7, near the tower'),
    ('md8_3',    'green',  'Sector 7, near the tower'),
    ('trnad_1',  'maroon', 'train scene'),
    ('trnad_2',  'maroon', 'train scene'),
    ('trnad_3',  'maroon', 'train scene'),
    ('hbfront',  'black',  'Honey Bee Inn -- THE CONTROL, already black'),
    ('hbinn',    'black',  'Honey Bee Inn -- THE CONTROL, already black'),
    ('hbmens',   'black',  'Honey Bee Inn -- THE CONTROL, already black'),
]

SECTION_NAMES = ['script', 'camera', 'models', 'PALETTE', 'walkmesh',
                 'tilemap', 'encounter', 'triggers', 'background']


def bucket(colour):
    """Coarse buckets, so "is this tan?" is answered the same way twice."""
    r, g, b, _m = BK.rgba(colour)
    if max(r, g, b) < 40:
        return 'black'
    if g > 180 and r < 90 and b < 90:
        return 'green'
    if r > 140 and g > 100 and b > 60 and abs(r - g) < 90 and b < g:
        return 'tan'
    if r > 90 and g < 80 and b < 80:
        return 'maroon'
    if abs(r - g) < 30 and abs(g - b) < 30:
        return 'grey'
    return 'other'


def read(path, only=None):
    """[{name, colours, n, section, err}] for every field in the archive."""
    arc = lgp.Archive(path)
    out = []
    for name in sorted(arc.names()):
        entry = arc.index.get(name)
        if entry is None or not arc.is_field(entry):
            continue
        if only and name.lower().split('.')[0] not in only:
            continue
        rec = {'name': name, 'colours': [], 'n': 0, 'section': None,
               'err': ''}
        try:
            parts = lgp.split_sections(arc.decompressed(entry))
            idx = BK.find_palette_section(parts)
            rec['section'] = idx
            cols = BK.entry0(parts[idx])
            rec['colours'] = cols
            rec['n'] = len(cols)
        except Exception as exc:                               # noqa: BLE001
            rec['err'] = str(exc)[:90]
        out.append(rec)
    return out


def dump(path, names):
    """Every section's length and first bytes, for one or more fields."""
    arc = lgp.Archive(path)
    want = {n.lower() for n in names}
    shown = 0
    for name in sorted(arc.names()):
        entry = arc.index.get(name)
        if entry is None or not arc.is_field(entry):
            continue
        if name.lower().split('.')[0] not in want:
            continue
        parts = lgp.split_sections(arc.decompressed(entry))
        print()
        print('%s -- %d section(s)' % (name, len(parts)))
        for i, sec in enumerate(parts):
            label = SECTION_NAMES[i] if i < len(SECTION_NAMES) else '?'
            head = sec[:24]
            words = ' '.join('%04X' % w for w in
                             __import__('struct').unpack_from(
                                 '<12H', sec.ljust(24, b'\0')))
            print('  %d %-11s len %8d  hex %s' % (i, label, len(sec),
                                                  head[:12].hex(' ')))
            print('  %-14s u16 %s' % ('', words))
            if len(sec) % 512 == 0:
                print('  %-14s -> %d bytes is exactly %d x 256 colours'
                      % ('', len(sec), len(sec) // 512))
            elif (len(sec) - 8) % 512 == 0 and len(sec) > 8:
                print('  %-14s -> (len-8) is exactly %d x 256 colours'
                      % ('', (len(sec) - 8) // 512))
            elif (len(sec) - 12) % 512 == 0 and len(sec) > 12:
                print('  %-14s -> (len-12) is exactly %d x 256 colours'
                      % ('', (len(sec) - 12) // 512))
        try:
            idx = BK.find_palette_section(parts)
            head, pages, cpp = BK.palette_block(parts[idx])
            print('  => palette is section %d: header %d, %d page(s) of %d'
                  % (idx, head, pages, cpp))
            print('  => entry 0 per page: %s'
                  % ', '.join(BK.hex_rgb(c) for c in BK.entry0(parts[idx])))
        except Exception as exc:                               # noqa: BLE001
            print('  => NO SECTION PARSES AS A PALETTE: %s' % exc)
        shown += 1
    if not shown:
        print('no field matched %s' % ', '.join(sorted(want)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('flevel', help='path to flevel.lgp (built or vanilla)')
    ap.add_argument('fields', nargs='*', help='field names; default all')
    ap.add_argument('--csv', metavar='PATH')
    ap.add_argument('--predict', action='store_true',
                    help='compare against the hardware reports')
    ap.add_argument('--dump', nargs='+', metavar='FIELD',
                    help='print the raw section layout and stop')
    a = ap.parse_args(argv)

    if a.dump:
        print('reading %s ...' % a.flevel)
        return dump(a.flevel, a.dump)

    only = {f.lower() for f in a.fields} or None
    if a.predict and not only:
        only = {n for n, _c, _w in REPORTED}

    print('reading %s ...' % a.flevel)
    rows = read(a.flevel, only)
    if not rows:
        print('! no fields read. Is that a flevel.lgp?')
        return 2

    bad = [r for r in rows if r['err']]
    good = [r for r in rows if not r['err']]

    if not good:
        print()
        print('! NOTHING PARSED -- %d field(s) refused.' % len(bad))
        for r in bad[:6]:
            print('    %-12s %s' % (r['name'], r['err']))
        print()
        print('Run this and paste the output; it settles the layout in one')
        print('round trip rather than three:')
        print('    python3 diag_bgkey.py %s --dump %s'
              % (a.flevel, bad[0]['name']))
        return 2

    if a.predict:
        got = {r['name'].lower().split('.')[0]: r for r in good}
        print()
        print('%-10s %-9s %-8s %-8s %s'
              % ('field', 'entry 0', 'predict', 'reported', 'where'))
        hit = miss = absent = 0
        for name, colour, where in REPORTED:
            r = got.get(name)
            if r is None or not r['colours']:
                print('%-10s %-9s %-8s %-8s %s'
                      % (name, '--', '--', colour,
                         where + '  (not in this archive)'))
                absent += 1
                continue
            c = r['colours'][0]
            pred = bucket(c)
            ok = (pred == colour)
            hit += ok
            miss += not ok
            print('%-10s %-9s %-8s %-8s %s%s'
                  % (name, BK.hex_rgb(c), pred, colour, where,
                     '' if ok else '   <-- DOES NOT MATCH'))
        print()
        print('%d match, %d do not, %d absent' % (hit, miss, absent))
        print()
        if miss == 0 and hit:
            print('THEORY CONFIRMED offline. The margin is palette entry 0.')
            print('Ship the side bar colour setting on "Black" and the bars')
            print('go black on every field in one build.')
        elif hit and miss:
            print('MIXED. Some margins are the palette and some are not.')
            print('Ship it anyway -- it can only help the ones that match --')
            print('but expect the mismatched fields to keep their colour,')
            print('and say which ones in the hardware report.')
        else:
            print('THEORY DEAD. The margin is not palette entry 0.')
            print('Do NOT ship the side bar colour setting. Re-open')
            print('HANDOFF-60 §3.2 and start from the field buffer instead.')
        return 0

    counts = collections.Counter()
    nonblack = []
    for r in good:
        if not r['colours']:
            continue
        c = r['colours'][0]
        counts[bucket(c)] += 1
        if c != BK.BLACK:
            nonblack.append(r)

    if only:
        for r in rows:
            if r['err']:
                print('%-12s  !! %s' % (r['name'], r['err']))
                continue
            cols = r['colours']
            print('%-12s  section %d, %d page(s), entry 0 = %s (%s)  '
                  'raw 0x%04X'
                  % (r['name'], r['section'], r['n'], BK.hex_rgb(cols[0]),
                     bucket(cols[0]), cols[0]))
            distinct = sorted(set(cols))
            if len(distinct) > 1:
                print('%14s across all pages: %s'
                      % ('', ', '.join(BK.hex_rgb(c) for c in distinct[:8])))
        return 0

    print()
    print('%d field(s) read, %d unparsed' % (len(good), len(bad)))
    print('  entry 0 is already black in %d' % (len(good) - len(nonblack)))
    print('  entry 0 is a VISIBLE colour in %d  <- these are the fields whose'
          % len(nonblack))
    print('     16:9 margins show that colour today')
    print()
    print('  by colour: ' + ', '.join('%s %d' % kv
                                      for kv in counts.most_common()))
    print()
    print('worst offenders (brightest entry 0 first):')

    def bright(r):
        rr, gg, bb, _m = BK.rgba(r['colours'][0])
        return rr + gg + bb

    for r in sorted(nonblack, key=bright, reverse=True)[:20]:
        print('  %-12s %s  %s' % (r['name'], BK.hex_rgb(r['colours'][0]),
                                  bucket(r['colours'][0])))
    if bad:
        print()
        print('unparsed:')
        for r in bad[:8]:
            print('  %-12s %s' % (r['name'], r['err']))

    if a.csv:
        import csv
        with open(a.csv, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['field', 'section', 'pages', 'entry0_hex',
                        'entry0_raw', 'bucket', 'already_black', 'error'])
            for r in rows:
                c = r['colours'][0] if r['colours'] else None
                w.writerow([r['name'], r['section'], r['n'],
                            BK.hex_rgb(c) if c is not None else '',
                            '0x%04X' % c if c is not None else '',
                            bucket(c) if c is not None else '',
                            1 if c == BK.BLACK else 0,
                            r['err']])
        print()
        print('wrote %s' % a.csv)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
