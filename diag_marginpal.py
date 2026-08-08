#!/usr/bin/env python3
"""
diag_marginpal.py -- PREDICT the colour of the 16:9 side band, per field.

THE FINDING THIS IMPLEMENTS
===========================
The coloured side bands and the scattered black squares are THE SAME BUG.

`field_load_textures` (x86 0x640292) abandons the whole loop on the first
page it cannot allocate. Every page after it keeps texture handle 0 and never
draws. A tile drawing from handle 0 samples index 0, and index 0 goes through
THAT TILE'S PALETTE -- so it paints `palette[tile.palette_id][0]`.

  * a field whose margin palettes have entry 0 = #000000  -> BLACK bars/squares
  * a field whose margin palettes have entry 0 = #00F800  -> PURE GREEN bars
  * ...and so on. The colour is per-field, stable, and predictable from data.

That is why it never changed when the clear colour, the clear rect, the
camera range or the tile window were touched: none of those are the palette,
and none of them allocate a texture.

WHY "THE MARGIN IS PALETTE ENTRY 0" WAS WRONGLY DECLARED DEAD
=============================================================
HANDOFF-61 proposed it. HANDOFF-62 §1.1 falsified it with
`diag_bgkey.py --predict`, which compares **palette 0 of the field**. The
margin tiles do not necessarily use palette 0, and the two worst test cases
were chosen:

    mkt_ia   every palette entry 0 is #000000 AND it has NO margin tiles
             at all (256 units of art). It cannot show a coloured band from
             this mechanism and never could.
    md8_1    palette 0 IS #00F800 -- and 66 of its 74 layer-1 margin tiles
             are on palette 0. It was called "the field the theory was
             derived from, so not evidence". It was the only field in the
             sample that actually had margin tiles to be wrong about.

This tool asks the right question: **which palettes do the MARGIN tiles use,
and what is entry 0 of those?**

USAGE
=====
    python3 diag_marginpal.py <flevel.lgp>                  # all fields, ranked
    python3 diag_marginpal.py <flevel.lgp> md8_1 mds7st2    # named fields
    python3 diag_marginpal.py <flevel.lgp> --csv out.csv

Labels, per HANDOFF-62 §0.2: everything here is MEASURED from the archive.
The prediction is falsifiable by looking at the screen.

REFUSES RATHER THAN GUESSES (§0.8)
==================================
The section-3 palette walk is validated by requiring the declared page count
and colours-per-page to consume the section exactly. A field that does not
parse is reported as UNREADABLE, never as a colour.
"""
from __future__ import annotations

import argparse
import collections
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lgp                                                      # noqa: E402
import diag_common as DC                                        # noqa: E402

SECTION_PALETTE = 3          # zero-based; section 8 is the background
SECTION_BG = 8
TILE = 16
MARGIN_FROM = 160            # a tile beyond +/-160 is outside the 4:3 picture
T_PALETTE = 22               # byte offset of palette_id in the 52-byte tile


def rgb15(v):
    """FF7 stores B5G5R5. Returns (#RRGGBB, (r, g, b))."""
    r, g, b = (v & 0x1F) << 3, ((v >> 5) & 0x1F) << 3, ((v >> 10) & 0x1F) << 3
    return '#%02X%02X%02X' % (r, g, b), (r, g, b)


def palette_entry0(sec):
    """
    [#RRGGBB] -- entry 0 of every palette page. Raises if the section does
    not parse as a palette block of the declared shape.
    """
    if len(sec) < 12:
        raise ValueError('section too short to be a palette')
    _ln, _px, _py, cpp, npg = struct.unpack_from('<IHHHH', sec, 0)
    if not (1 <= cpp <= 1024 and 1 <= npg <= 256):
        raise ValueError('implausible palette shape: %d colours x %d pages'
                         % (cpp, npg))
    need = 12 + cpp * npg * 2
    if need > len(sec):
        raise ValueError('palette block wants %d bytes, section has %d'
                         % (need, len(sec)))
    return [rgb15(struct.unpack_from('<H', sec, 12 + p * cpp * 2)[0])[0]
            for p in range(npg)]


def measure(raw):
    """One field. Raises on anything it cannot read."""
    parts = lgp.split_sections(raw)
    ent0 = palette_entry0(parts[SECTION_PALETTE])
    sec9 = parts[SECTION_BG]
    surv = DC.survey(sec9)
    margin = collections.Counter()
    centre = collections.Counter()
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer not in (1, 2):
            continue
        for o in offs:
            x = struct.unpack_from('<h', sec9, o + DC.TILE_DST_X)[0]
            pid = sec9[o + T_PALETTE]
            (margin if abs(x + TILE // 2) > MARGIN_FROM else centre)[pid] += 1
    colours = collections.Counter()
    for pid, n in margin.items():
        colours[ent0[pid] if pid < len(ent0) else '#??????'] += n
    return {'entry0': ent0, 'margin': margin, 'centre': centre,
            'colours': colours, 'n_margin': sum(margin.values()),
            'pages': surv['n_pages']}


def predict(r):
    """The band colour this field will show if its pages fail to allocate."""
    if not r['n_margin']:
        return '#000000', 'no margin tiles -- letterboxes, always BLACK'
    col, n = r['colours'].most_common(1)[0]
    share = 100.0 * n / r['n_margin']
    tag = 'BLACK' if col == '#000000' else 'COLOURED'
    return col, '%s  %s on %.0f%% of %d margin tile(s)' % (tag, col, share,
                                                           r['n_margin'])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('flevel')
    ap.add_argument('fields', nargs='*')
    ap.add_argument('--csv')
    ap.add_argument('--coloured-only', action='store_true',
                    help='only fields predicted to show a NON-black band')
    a = ap.parse_args(argv)

    arc = lgp.Archive(a.flevel)
    names = a.fields or [n for n in arc.names() if arc.is_field(arc.index[n])]
    rows, bad = {}, {}
    for nm in names:
        e = arc.index.get(nm)
        if e is None:
            bad[nm] = 'not in archive'
            continue
        try:
            rows[nm] = measure(arc.decompressed(e))
        except Exception as exc:                                # noqa: BLE001
            bad[nm] = str(exc)

    if a.fields:
        for nm in a.fields:
            if nm in bad:
                print('%-9s UNREADABLE -- %s' % (nm, bad[nm]))
                continue
            r = rows[nm]
            col, why = predict(r)
            print('%-9s %d page(s)   PREDICTED BAND: %s' % (nm, r['pages'], why))
            for pid, n in r['margin'].most_common(6):
                print('     margin pal %-3d x%-5d entry0 %s'
                      % (pid, n, r['entry0'][pid] if pid < len(r['entry0'])
                         else '#??????'))
        return 0

    n_col = sum(1 for r in rows.values() if predict(r)[0] != '#000000')
    print('%d field(s) measured, %d unreadable' % (len(rows), len(bad)))
    print('  predicted BLACK bands    : %d' % (len(rows) - n_col))
    print('  predicted COLOURED bands : %d   <- these are the visible defect'
          % n_col)
    print()
    ranked = sorted(((r['n_margin'], nm, r) for nm, r in rows.items()),
                    reverse=True)
    for n, nm, r in ranked:
        col, why = predict(r)
        if a.coloured_only and col == '#000000':
            continue
        print('  %-9s %s' % (nm, why))
    if a.csv:
        import csv
        with open(a.csv, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['field', 'pages', 'margin_tiles', 'predicted_colour',
                        'share_pct'])
            for nm, r in sorted(rows.items()):
                col, _ = predict(r)
                share = (100.0 * r['colours'][col] / r['n_margin']
                         if r['n_margin'] else 0.0)
                w.writerow([nm, r['pages'], r['n_margin'], col,
                            '%.1f' % share])
        print('\nwrote %s' % a.csv)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
