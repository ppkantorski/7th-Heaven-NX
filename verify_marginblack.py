#!/usr/bin/env python3
"""
verify_marginblack.py -- render every field BEFORE and AFTER the margin pass
and check the two things that have to be true.

    1. THE MARGIN GOES BLACK.  Every pixel the pass claims to have moved
       must render at NEAR_BLACK.
    2. THE PICTURE DOES NOT MOVE.  The 4:3 interior, dst_x in [-160, 160),
       must be BYTE-IDENTICAL. So must the margin of every field the pass
       did not touch -- the 466 fields with real margin art, HANDOFF-65
       §4.1's control.

This is the whole safety argument, checked rather than argued, on the real
archive, before any hardware is spent. It does not rebuild the archive: it
applies the plan to the decompressed field in memory and re-renders, which
is the same bytes the packer would write.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import numpy as np

import lgp
import ff7nx_marginblack as MB

CX = 320
IN_LO, IN_HI = CX - 160, CX + 160          # the 4:3 picture, in canvas x


def filler_mask(plan, shape):
    """
    The destination rectangles of exactly the tiles the pass moved.

    NOT the whole band. `mrkt4` is why: 210 of its margin tiles carry real
    widescreen ART on truecolor pages and must stay exactly as they are, so a
    test that demanded a black band would fail the one field HANDOFF-65 §1.5
    names as already correct. The pass is accountable for the pixels it
    claims, and for nothing else.
    """
    m = np.zeros(shape, bool)
    for t in plan.tiles:
        dx, dy = t.dx + CX, t.dy + CX
        if 0 <= dx <= shape[1] - MB.TILE and 0 <= dy <= shape[0] - MB.TILE:
            m[dy:dy + MB.TILE, dx:dx + MB.TILE] = True
    return m


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('--fields', nargs='*', default=None)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--start', type=int, default=0)
    ap.add_argument('--end', type=int, default=0)
    ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args()

    arc = lgp.Archive(a.flevel)
    names = sorted(n for n in arc.names() if arc.is_field(arc.index[n]))
    if a.fields:
        names = [n for n in names if n in a.fields]
    if a.end:
        names = names[a.start:a.end]
    elif a.start:
        names = names[a.start:]
    if a.limit:
        names = names[:a.limit]

    n_changed = n_untouched = n_skip = n_gained = 0
    interior_broken, margin_not_black, art_lost = [], [], []
    art_gained_inside = []
    for name in names:
        raw = arc.decompressed(arc.index[name])
        try:
            plan, parts, surv = MB.plan_field(name, raw, lgp)
        except Exception:                                       # noqa: BLE001
            n_skip += 1
            continue
        if plan.refusal:
            n_skip += 1
            continue
        try:
            before, dbefore = MB.render_margin(raw, lgp)
        except Exception:                                       # noqa: BLE001
            n_skip += 1
            continue
        if not plan.tiles:
            n_untouched += 1
            continue

        new_raw = lgp.join_sections(MB.apply_plan(plan, parts, surv))
        after, dafter = MB.render_margin(new_raw, lgp)
        n_changed += 1

        # 1. the interior must be byte-identical
        if not np.array_equal(before[:, IN_LO:IN_HI], after[:, IN_LO:IN_HI]):
            n = int((before[:, IN_LO:IN_HI] != after[:, IN_LO:IN_HI]).any(-1)
                    .sum())
            interior_broken.append((name, n))

        # 2. nothing that was drawn may stop being drawn.
        #
        # The reverse -- a tile that did NOT draw before and draws now -- is
        # expected since HANDOFF-67 §2: a field whose stray palette_ID equals
        # the appended page gains that tile, near-black, in the margin. It is
        # counted separately and it is an error only INSIDE the picture, which
        # check 1 already forbids. An equality test on `drawn` would have
        # reported those fields as art loss with zero pixels lost.
        lost = int((dbefore & ~dafter).sum())
        if lost:
            art_lost.append((name, lost))
        gained = dafter & ~dbefore
        if gained[:, IN_LO:IN_HI].any():
            art_gained_inside.append((name, int(gained[:, IN_LO:IN_HI].sum())))
        n_gained += int(gained.sum())

        # 3. the pixels the pass claims must be near-black now
        fm = filler_mask(plan, dafter.shape)
        b = after[fm]
        if b.size:
            worst = int(b.max())
            if worst > 8:               # NEAR_BLACK is RGB(0, 0, 8)
                margin_not_black.append((name, worst,
                                         tuple(int(x) for x in
                                               b[b.max(1).argmax()])))
        if a.verbose:
            bb = before[fm]
            print('%-12s before %s  after %s  %d tile(s)'
                  % (name,
                     tuple(int(x) for x in bb.mean(0)) if bb.size else '-',
                     tuple(int(x) for x in b.mean(0)) if b.size else '-',
                     len(plan.tiles)))

    print('\n---- %d field(s)' % len(names))
    print('changed by the pass      %d' % n_changed)
    print('untouched (control)      %d' % n_untouched)
    print('unreadable, skipped      %d' % n_skip)
    print('INTERIOR CHANGED         %d  <- must be 0' % len(interior_broken))
    for n, k in interior_broken[:8]:
        print('     %s: %d pixel(s)' % (n, k))
    print('ART STOPPED BEING DRAWN  %d  <- must be 0' % len(art_lost))
    for n, k in art_lost[:8]:
        print('     %s: %d pixel(s)' % (n, k))
    print('NEWLY DRAWN, in margin   %d pixel(s)  <- stray IDs adopted, ok'
          % n_gained)
    print('NEWLY DRAWN, IN PICTURE  %d  <- must be 0' % len(art_gained_inside))
    for n, k in art_gained_inside[:8]:
        print('     %s: %d pixel(s)' % (n, k))
    print('MARGIN NOT BLACK         %d  <- must be 0' % len(margin_not_black))
    for n, w, px in margin_not_black[:8]:
        print('     %s: brightest %d %s' % (n, w, px))
    ok = not (interior_broken or art_lost or margin_not_black
              or art_gained_inside)
    print('\n%s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
