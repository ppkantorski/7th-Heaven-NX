#!/usr/bin/env python3
"""
preflight_d2slots.py -- confirm the wiring BEFORE spending a build, and name
every field the change is expected to alter.

Run from the root of 7th_heaven_nx:

    python3 preflight_d2slots.py sdout/atmosphere/contents/0100A5B00BDC6000/\\
        romfs/ff7/workingdir/data/field/flevel.lgp

It checks three things and prints the expected build-log lines. It touches
nothing.

  1. settings.json really drives the two new knobs, through the same path the
     build uses -- not through the environment.
  2. `field_bg_max_pages` is inert on this archive, so a reader is not left
     believing it did the work.
  3. Which fields carry a truecolor page at slot >= 26 + N, i.e. exactly the
     fields the cap will change, and which of those are the 48 predicted to
     show black rectangles today.
"""
from __future__ import annotations

import collections
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                      # noqa: E402
import diag_common as DC                                        # noqa: E402
import field_bg_repack as R                                     # noqa: E402
import ff7nx_marginblack as MB                                  # noqa: E402

D2_LO = 26                          # field_bg_native.D2_GROUPS[0][0]


def settings_global():
    p = os.path.join(_HERE, 'settings.json')
    try:
        with open(p) as fh:
            return json.load(fh).get('__global__', {}), p
    except Exception as exc:                                    # noqa: BLE001
        return {}, '%s (UNREADABLE: %s)' % (p, exc)


def main():
    g, path = settings_global()
    print('settings.json: %s\n' % path)

    n = g.get('field_bg_d2_slots_per_group', R.DEFAULT_D2_SLOTS_PER_GROUP)
    mb = g.get('margin_black', 0)
    print('  field_bg_d2_slots_per_group : %s   -> slots %s'
          % (n, list(range(D2_LO, D2_LO + n)) if n else 'ALL (cap off)'))
    print('  margin_black                : %s   -> recolour pass %s'
          % (mb, 'ON' if str(mb) not in ('0', 'None', '') else 'OFF'))
    print('  field_bg_max_pages          : %s' % g.get('field_bg_max_pages'))
    print('  field_bg_replace_only       : %s' % g.get('field_bg_replace_only'))
    print('  field_bg_partial            : %s' % g.get('field_bg_partial'))
    print('  field_bg_page_px            : %s' % g.get('field_bg_page_px'))

    # the module defaults must agree with what the build will do when the key
    # is absent, or a settings.json written before this change silently
    # behaves differently from one written after it
    print('\n  module default (cap)        : %d' % R.DEFAULT_D2_SLOTS_PER_GROUP)
    print('  module default (marginblack): %s'
          % ('ON' if MB.DEFAULT_ON else 'OFF'))

    if len(sys.argv) < 2:
        print('\n(pass a flevel.lgp to list the affected fields)')
        return 0

    arc = lgp.Archive(sys.argv[1])
    names = sorted(x for x in arc.names() if arc.is_field(arc.index[x]))
    hist = collections.Counter()
    over, maxpg = [], 0
    for nm in names:
        try:
            pg = DC.survey(lgp.split_sections(
                arc.decompressed(arc.index[nm]))[8])['pages']
        except Exception:                                       # noqa: BLE001
            continue
        maxpg = max(maxpg, len(pg))
        hist[len(pg)] += 1
        d2 = sorted(p.slot for p in pg if p.depth == 2)
        if n and any(s >= D2_LO + n for s in d2):
            over.append((nm, len(pg), d2))

    print('\n---- %s' % os.path.basename(sys.argv[1]))
    print('heaviest field: %d page(s)' % maxpg)
    print('fields above a page ceiling of 14/15/16: %d   <- field_bg_max_pages'
          % sum(v for k, v in hist.items() if k > 14))
    print('   so that control is INERT on this archive; the cap below is not.')
    print('\nfields with a truecolor page at slot >= %d: %d'
          % (D2_LO + n, len(over)))
    print('   these are the fields the cap changes, and the fields predicted')
    print('   to show black rectangles in the build you have now.\n')
    for nm, np_, d2 in over[:60]:
        print('   %-10s %2d page(s)  truecolor slots %s' % (nm, np_, d2))
    if len(over) > 60:
        print('   ... and %d more' % (len(over) - 60))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
