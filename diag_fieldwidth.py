#!/usr/bin/env python3
"""
diag_fieldwidth.py -- does this field's ART reach the edges of a 16:9 frame?

    python3 diag_fieldwidth.py <flevel.lgp>                  # every field
    python3 diag_fieldwidth.py <flevel.lgp> md1stin md8_1    # named fields
    python3 diag_fieldwidth.py <flevel.lgp> --csv out.csv
    python3 diag_fieldwidth.py <built.lgp> --against <dump/flevel.lgp>

WHY
===
An empty band down the side of a widescreen field has exactly two possible
causes and they need opposite fixes:

  1. The tile window is culling art that exists.  -> widen the window.
  2. The field has no art out there at all.       -> nothing to draw; the
     honest answer is to letterbox that field, not to widen it.

Guessing between them costs a reboot each time. This reads the tile lists
straight out of flevel.lgp and measures it, on the bench, in about a minute
for all 711 fields.

THE ARITHMETIC
==============
A field background tile record carries `dst_x` in the field's own 320-wide
space. `field_layer1_pick_tiles` draws it at

    game_x = ((320 - cam_x) + tile.x) * mult          mult = 2 in mode 2

so a tile is on screen when `tile.x` is inside the window the cull leaves
open. Stock (`ff7nx_wsclamp` names in brackets):

    cam_x - 336 [left] < tile.x < cam_x [right]       320 units wide

and the 16:9 build opens it to `cam-376 .. cam+64`, 440 units. What 16:9
actually NEEDS is 640/WS_SCALE / mult:

    WS_SCALE 0.74766355 (buffer 428)  ->  428 units
    WS_SCALE 0.74941452 (buffer 854)  ->  427 units
    WS_SCALE 0.75       (buffer 1280) ->  426.67 units

So: if a field's tiles span fewer than ~428 units in x, **no camera position
and no tile window can fill a 16:9 frame** -- the art is not there. If they
span more, it can, and an empty band means either the window or the camera
clamp.

Vertical is reported too, because `SEVENTH_NX_WS_UNCROP` asks the same
question about the 24 px letterbox: 480/mult = 240 units are needed and
vanilla art is 224.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lgp                                                      # noqa: E402
import diag_common as DC                                        # noqa: E402

MULT = 2                      # field_bg_multiplier in mode 2
SECTION9 = 8                  # zero-based; section 9 is the background

# Width, in TILE units, that each field-buffer preset needs. 640 / WS_SCALE
# / MULT, i.e. exactly half the visible game span.
NEEDED = {1: 428.0, 2: 427.0, 3: 1280.0 * 2 / 3 / MULT}
STOCK_WINDOW = 320.0          # what 4:3 shows
TILE_PX = 16                  # a tile is 16x16 in field space

# What the shipping tile window admits, from ff7nx_wsclamp.defaults().
SHIPPING_WINDOW = 376 + 64    # left + right


tile_extents = DC.tile_extents
SECTION9 = DC.SECTION9


def measure(path, only=None, log=print):
    """[{name, n, span_x, span_y, ...}] for every field that parses."""
    arc = lgp.Archive(path)
    out = []
    skipped = []
    names = sorted(arc.index)
    for i, key in enumerate(names):
        entry = arc.index[key]
        if only and entry['name'].lower() not in only:
            continue
        if not arc.is_field(entry):
            continue
        try:
            raw = arc.decompressed(entry)
            sections = lgp.split_sections(raw)
            ext = tile_extents(sections[SECTION9])
        except Exception as exc:                               # noqa: BLE001
            skipped.append((entry['name'], str(exc)[:60]))
            continue
        if ext is None:
            skipped.append((entry['name'], 'no tiles'))
            continue
        span_x = ext['x'][1] - ext['x'][0]
        span_y = ext['y'][1] - ext['y'][0]
        out.append({'name': entry['name'], 'n': ext['n'],
                    'pages': ext['pages'], 'layers': ext['layers'],
                    'x0': ext['x'][0], 'x1': ext['x'][1], 'span_x': span_x,
                    'y0': ext['y'][0], 'y1': ext['y'][1], 'span_y': span_y})
        if log and not only and (i % 150 == 0) and i:
            log('    ... %d/%d' % (i, len(names)))
    return out, skipped


def verdict(span_x, need):
    if span_x >= SHIPPING_WINDOW:
        return 'ART OK      (window is the limit, if anything)'
    if span_x >= need:
        return 'ART OK      (%.0f units spare)' % (span_x - need)
    if span_x <= STOCK_WINDOW:
        return 'ART ABSENT  (4:3 only -- %.0f units short)' % (need - span_x)
    return 'ART SHORT   (%.0f of %.0f units -- %.0f short)' % (
        span_x, need, need - span_x)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('flevel', help='path to flevel.lgp (built or vanilla)')
    ap.add_argument('fields', nargs='*', help='field names; default all')
    ap.add_argument('--scale', type=int, default=1, choices=(1, 2, 3),
                    help='which field-buffer preset to measure against')
    ap.add_argument('--against', metavar='FLEVEL',
                    help='a second flevel to compare with -- e.g. the '
                         'vanilla dump, to see whether the mod widened '
                         'anything at all')
    ap.add_argument('--csv', metavar='PATH')
    ap.add_argument('--worst', type=int, default=25,
                    help='how many short fields to list (default 25)')
    a = ap.parse_args(argv)

    need = NEEDED[a.scale]
    only = {f.lower() for f in a.fields} or None

    print('reading %s ...' % a.flevel)
    rows, skipped = measure(a.flevel, only)
    if not rows:
        print('! no fields parsed. Is that a flevel.lgp?')
        for n, why in skipped[:5]:
            print('    %s: %s' % (n, why))
        return 2

    base = {}
    if a.against:
        print('reading %s ...' % a.against)
        brows, _ = measure(a.against, only)
        base = {r['name'].lower(): r for r in brows}

    print()
    print('16:9 at field-buffer %dx needs %.0f tile units of art across.'
          % (a.scale, need))
    print('The shipping tile window admits %d. 4:3 shows %d.'
          % (SHIPPING_WINDOW, STOCK_WINDOW))
    print()

    if only:
        for r in sorted(rows, key=lambda r: r['name']):
            print('%-10s  %5d tiles, %2d page(s)' % (r['name'], r['n'],
                                                     r['pages']))
            print('            x %5d .. %-5d  span %4d   %s'
                  % (r['x0'], r['x1'], r['span_x'],
                     verdict(r['span_x'], need)))
            print('            y %5d .. %-5d  span %4d   (240 needed for a '
                  'full-height frame; vanilla art is 224)'
                  % (r['y0'], r['y1'], r['span_y']))
            for layer in sorted(r['layers']):
                v = r['layers'][layer]
                print('            layer %d  %5d tile(s)  x %5d .. %-5d '
                      ' span %4d'
                      % (layer, v['n'], v['x'][0], v['x'][1],
                         v['x'][1] - v['x'][0]))
            b = base.get(r['name'].lower())
            if b:
                print('            vanilla span %d -> %d   (%+d)'
                      % (b['span_x'], r['span_x'], r['span_x'] - b['span_x']))
            print()
        return 0

    absent = [r for r in rows if r['span_x'] <= STOCK_WINDOW]
    short = [r for r in rows if STOCK_WINDOW < r['span_x'] < need]
    ok = [r for r in rows if r['span_x'] >= need]
    print('%d field(s) measured' % len(rows))
    print('  %4d can fill 16:9            (span >= %.0f)' % (len(ok), need))
    print('  %4d are SHORT                (some extra art, not enough)'
          % len(short))
    print('  %4d have NO art past 4:3     (span <= %d)'
          % (len(absent), STOCK_WINDOW))
    if skipped:
        print('  %4d did not parse' % len(skipped))
    print()
    print('Fields that CANNOT fill the frame, worst first -- these are the '
          'ones that will')
    print('show an empty band no matter what the tile window does:')
    for r in sorted(absent + short, key=lambda r: r['span_x'])[:a.worst]:
        b = base.get(r['name'].lower())
        delta = ('   vanilla %d' % b['span_x']) if b else ''
        print('  %-10s span %4d   short by %4.0f%s'
              % (r['name'], r['span_x'], need - r['span_x'], delta))
    if base:
        widened = [r for r in rows
                   if r['name'].lower() in base
                   and r['span_x'] > base[r['name'].lower()]['span_x']]
        print()
        print('%d field(s) have MORE art than vanilla (the mod repainted '
              'them wider)' % len(widened))

    if a.csv:
        import csv
        with open(a.csv, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['field', 'tiles', 'pages', 'x0', 'x1', 'span_x',
                        'y0', 'y1', 'span_y', 'needed', 'verdict'])
            for r in sorted(rows, key=lambda r: r['name']):
                w.writerow([r['name'], r['n'], r['pages'], r['x0'], r['x1'],
                            r['span_x'], r['y0'], r['y1'], r['span_y'],
                            '%.0f' % need, verdict(r['span_x'], need)])
        print()
        print('wrote %s' % a.csv)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
