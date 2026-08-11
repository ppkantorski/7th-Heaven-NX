#!/usr/bin/env python3
"""
diag_windowcap.py -- how many tiles name one page INSIDE ONE SCREENFUL.

WHY THIS EXISTS
===============
`field_bg_pagecap.effective_cap` grandfathers vanilla: `max(256, vanilla's
worst page)`. Its own docstring gives the reason -- `add_page_tile` is called
once per tile SUBMITTED THIS FRAME, so a scrolling field can name a page from
1,912 tiles and never overrun, because only a screenful is ever submitted.

The rule compares FILE counts. The risk is a FRAME count. Those are the same
number only when the field is one screen wide. MEASURED on md8_1:

    vanilla per page   {0: 671, 1: 219}      worst 671
    built   per page   {0: 416, 1: 269, ...} worst 416

so the cap sees 416 <= 671, does nothing, and the built field ships a page
named by 269 tiles in a room that is 448x240 -- one screenful at 16:9. Tiles
257+ run off the end of the 0x1804-byte page record and into the next page's
counter (FINDINGS-110), and the next page in load order is slot 2, which is
where 11 of the 12 black squares on screen sample from.

This measures the quantity that actually matters: the maximum, over every
window position, of tiles naming one page within the window.

    python3 diag_windowcap.py <flevel.lgp> md8_1
    python3 diag_windowcap.py <flevel.lgp> --all --vanilla <vanilla.lgp>

Writes nothing.
"""
from __future__ import annotations

import argparse
import collections
import os
import struct
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC              # noqa: E402
import lgp                            # noqa: E402

TILE = 16
T_DSTX, T_DSTY, T_TEX, T_TEX2 = 2, 4, 32, 34

# the 16:9 field window, in game units. 448x240 is the widened tile extent;
# the visible window measured by aligning render_field to a 1280x720 capture
# is 426x240 starting at dx=-213.
WIN_W = 426
WIN_H = 240
HARD_CAP = 256


def tiles(sec9, src_px=None):
    pages, tex_start, _e, _px = DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    out = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        for o in offs:
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            dy = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            slot = sec9[o + T_TEX]
            fx = sec9[o + T_TEX2]
            eff = fx if (fx and fx in pmap) else slot
            out.append((layer, eff, dx, dy))
    return out


def worst_window(sec9, win_w=WIN_W, win_h=WIN_H, src_px=None):
    """
    (worst_count, page, (x, y)) -- the most tiles naming any one page inside
    any win_w x win_h window.

    A tile counts when its 16x16 box overlaps the window. Candidate window
    origins are the distinct tile edges, which is exact: the count can only
    change where a tile enters or leaves.
    """
    ts = tiles(sec9, src_px)
    if not ts:
        return 0, None, (0, 0)
    xs = sorted({t[2] for t in ts} | {t[2] - win_w + TILE for t in ts})
    ys = sorted({t[3] for t in ts} | {t[3] - win_h + TILE for t in ts})
    by_page = collections.defaultdict(list)
    for _l, eff, dx, dy in ts:
        by_page[eff].append((dx, dy))
    best = (0, None, (0, 0))
    for page, pts in by_page.items():
        arr = np.array(pts, np.int32)
        for x in xs:
            inx = (arr[:, 0] + TILE > x) & (arr[:, 0] < x + win_w)
            if not inx.any():
                continue
            col = arr[inx, 1]
            for y in ys:
                n = int(((col + TILE > y) & (col < y + win_h)).sum())
                if n > best[0]:
                    best = (n, page, (x, y))
    return best


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('fields', nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--win', default=f'{WIN_W}x{WIN_H}')
    ap.add_argument('--cap', type=int, default=HARD_CAP)
    a = ap.parse_args(argv)
    win_w, win_h = (int(v) for v in a.win.lower().split('x'))

    A = lgp.Archive(a.flevel)
    names = list(A.index) if a.all else a.fields
    over = []
    print(f'window {win_w}x{win_h}, cap {a.cap}')
    for name in names:
        try:
            sec9 = lgp.split_sections(A.decompressed(A.index[name]))[8]
            n, page, at = worst_window(sec9, win_w, win_h, a.px)
        except Exception as exc:                              # noqa: BLE001
            if not a.all:
                print(f'{name}: {exc}')
            continue
        flag = '  >>> OVER' if n > a.cap else ''
        if n > a.cap:
            over.append((n, name, page))
        if not a.all or n > a.cap:
            print(f'  {name:<10} worst in-window {n:>5} on page {page} '
                  f'at {at}{flag}')
    if a.all:
        print(f'\n  fields over {a.cap} in one window: {len(over)} '
              f'of {len(names)}')
        for n, name, page in sorted(over, reverse=True)[:40]:
            print(f'    {name:<10} {n:>5} on page {page}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
