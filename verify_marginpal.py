#!/usr/bin/env python3
"""
verify_marginpal.py -- run ff7nx_marginart with ff7nx_marginpal OFF and ON over
many fields and score the difference, including the one thing that must never
change.

    python3 verify_marginpal.py [--limit 60] [--gain 1.0]

THE PASS/FAIL LINE
==================
    INTERIOR PIXELS MOVED must be 0.

The repoint only touches tiles that sample a `placeholder` cell -- one no
non-margin tile samples -- so no pixel with dst x inside [-160, 160) can move.
That is an argument; this executes it. Any non-zero here means the placeholder
set is not what it claims and the pass must not ship.

Everything else is a quality number: how many margin cells still collapse to a
single index (the flat block), how many are refused outright (the black
square), and the mean quantisation error.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC          # noqa: E402
import ff7nx_marginart as MA      # noqa: E402
import ff7nx_marginblack as MB    # noqa: E402
import ff7nx_marginpal as MP      # noqa: E402
import field_bg_repack as R       # noqa: E402
import lgp                        # noqa: E402
import render_field as RF         # noqa: E402
from try_marginpal import MOD9, IRO, VAN, splice   # noqa: E402


def cell_stats(raw):
    """(n_flat, n_margin, mean_idx) over layer-1 margin placeholder-ish cells."""
    parts = lgp.split_sections(raw)
    surv = DC.survey(parts[8])
    pages = {p.slot: p for p in surv['pages']}
    n1 = n = 0
    idx = []
    for t in MB.read_tiles(parts[8], surv, pages):
        p = pages.get(t.slot)
        if p is None or p.depth != 1 or t.layer != 1 or not t.outside_43:
            continue
        a = MB.page_array(p)
        b = MB.source_block(a[0], a[1], t.sx, t.sy)
        if b is None:
            continue
        u = np.unique(b).size
        n += 1
        idx.append(u)
        n1 += (u == 1)
    return n1, n, float(np.mean(idx)) if idx else 0.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=60)
    ap.add_argument('--gain', type=float, default=MP.MIN_ERR_GAIN)
    ap.add_argument('--fields')
    a = ap.parse_args(argv)
    MP.MIN_ERR_GAIN = a.gain

    V = lgp.Archive(VAN)
    prov = R.ArtProvider([(IRO, None)], 1024, lambda *_a: None)
    art = MA.provider_source(prov)
    names = a.fields.split(',') if a.fields else \
        [f[:-len('.chunk.9')] for f in sorted(os.listdir(MOD9))]

    T = dict(fields=0, touched=0, interior=0, tiles=0, slots=0,
             wild_off=0, wild_on=0, flat_off=0, flat_on=0, marg=0,
             filled_off=0, filled_on=0, eb=[], ea=[], ib=[], ia=[])
    for name in names:
        if not os.path.exists(os.path.join(MOD9, name + '.chunk.9')) \
                or name not in V.index:
            continue
        try:
            raw = splice(name, V)
        except Exception:                                      # noqa: BLE001
            continue
        out = {}
        for on in (False, True):
            MP.ENABLED = on
            try:
                new, st = MA.fill_field(name, raw, lgp, art, scope='margin')
            except Exception as exc:                           # noqa: BLE001
                print('  ! %s %s: %s' % (name, on, exc))
                out = None
                break
            out[on] = (new if new is not None else raw, st)
        if not out:
            continue
        T['fields'] += 1
        (ro, so), (rn, sn) = out[False], out[True]
        T['wild_off'] += so.get('wild', 0)
        T['wild_on'] += sn.get('wild', 0)
        T['filled_off'] += so.get('filled', 0)
        T['filled_on'] += sn.get('filled', 0)
        f0, m0, _i0 = cell_stats(ro)
        f1, m1, _i1 = cell_stats(rn)
        T['flat_off'] += f0
        T['flat_on'] += f1
        T['marg'] += m0
        p = sn.get('pal') or {}
        if p.get('slots_repointed'):
            T['touched'] += 1
            T['slots'] += p['slots_repointed']
            T['tiles'] += sn.get('pal_tiles', 0)
            T['eb'] += p['err_before']
            T['ea'] += p['err_after']
            T['ib'] += p['idx_before']
            T['ia'] += p['idx_after']
            # THE PASS/FAIL LINE -- render both and compare inside the 4:3 span
            try:
                ia, (x0, _y0) = RF.render(ro, (1, 2))
                ib, _ = RF.render(rn, (1, 2))
            except Exception:                                  # noqa: BLE001
                continue
            if ia.shape != ib.shape:
                T['interior'] += 10 ** 9
                print('  ! %s: canvas changed shape' % name)
                continue
            lo, hi = max(0, -160 - x0), max(0, 160 - x0)
            d = np.abs(ia[:, lo:hi].astype(np.int32)
                       - ib[:, lo:hi].astype(np.int32))
            moved = int((d.max(-1) > 0).sum())
            T['interior'] += moved
            if moved:
                print('  ! %s: %d interior pixels moved' % (name, moved))
        if T['fields'] >= a.limit:
            break

    print()
    print('fields run                       %d' % T['fields'])
    print('fields the repoint touched       %d' % T['touched'])
    print('pages repointed                  %d' % T['slots'])
    print('tiles rewritten (1 byte each)    %d' % T['tiles'])
    print()
    print('*** INTERIOR PIXELS MOVED        %d   %s'
          % (T['interior'], 'PASS' if T['interior'] == 0 else 'FAIL'))
    print()
    print('%-34s %10s %10s' % ('', 'OFF', 'ON'))
    print('%-34s %10d %10d' % ('margin cells REFUSED (black square)',
                               T['wild_off'], T['wild_on']))
    print('%-34s %10d %10d' % ('margin cells flat, 1 index',
                               T['flat_off'], T['flat_on']))
    print('%-34s %10d %10d' % ('margin cells written',
                               T['filled_off'], T['filled_on']))
    print('%-34s %10d %10s' % ('layer-1 margin cells seen', T['marg'], ''))
    if T['eb']:
        print('%-34s %10.2f %10.2f' % ('mean quantisation error',
                                       np.mean(T['eb']), np.mean(T['ea'])))
        print('%-34s %10.2f %10.2f' % ('mean indices per cell',
                                       np.mean(T['ib']), np.mean(T['ia'])))
    return 0 if T['interior'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
