#!/usr/bin/env python3
"""
diag_blackcells.py -- for one field in a BUILT flevel.lgp, list every layer-1
cell that renders BLACK, and say exactly which page slot, palette and depth
put it there.

    python3 diag_blackcells.py <built flevel.lgp> md8_1
    python3 diag_blackcells.py <built.lgp> md8_1 --vanilla <vanilla.lgp>

WHY
===
HANDOFF-121 section 6 names two branches and one measurement that settles them:

  * if every black cell samples ONE page slot, and that slot is late in load
    order, the fault is field_load_textures abandoning its loop -- no change to
    the archive's CONTENTS can fix it.
  * if the black cells are spread over several slots, the fault is still data,
    and the prime suspect is the depth-2 path: a promoted cell whose art is
    transparent writes 0x0000, which on a truecolor page MEANS transparent,
    i.e. nothing drawn, i.e. black.

This prints the slot histogram, the depth split, and the 0x0000 count, so the
branch is decided by a number instead of by a screenshot.
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

import diag_common as _DC          # noqa: E402
import ff7nx_marginblack as MB     # noqa: E402
import lgp                         # noqa: E402

TILE = 16
UV_SCALE = 10_000_000
T_DSTX, T_DSTY = 2, 4
T_PAL = 22
T_TEX, T_TEX2 = 32, 34
T_SRC_X_BIG, T_SRC_Y_BIG = 42, 46

# "black" for reporting purposes: the cell puts nothing visible on screen.
BLACK_MAX = 8          # per-channel 0-255
BLACK_FRAC = 0.90      # this share of the cell's pixels at or under BLACK_MAX


def _pal_rgb(sec3):
    cols, hdr, npg, cpp = MB.palette_colours(sec3)
    v = cols.astype(np.uint32).reshape(npg, cpp)
    return np.stack([((v & 31) << 3).astype(np.uint8),
                     (((v >> 5) & 31) << 3).astype(np.uint8),
                     (((v >> 10) & 31) << 3).astype(np.uint8)], -1)


def _d2_rgb(buf):
    v = buf.astype(np.uint32)
    return np.stack([(((v >> 11) & 31) << 3).astype(np.uint8),
                     (((v >> 5) & 63) << 2).astype(np.uint8),
                     ((v & 31) << 3).astype(np.uint8)], -1)


def scan(raw, layers=(1,)):
    parts = lgp.split_sections(raw)
    sec9 = parts[8]
    pages, tex_start, _tex_end, px = _DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    pal = _pal_rgb(parts[3])
    npg_pal = pal.shape[0]

    raw_pages, rgb_pages = {}, {}
    for s, p in pmap.items():
        if p.depth == 1:
            raw_pages[s] = np.frombuffer(p.data, np.uint8).reshape(256, 256)
        else:
            u16 = np.frombuffer(p.data, '<u2').reshape(p.px, p.px)
            raw_pages[s] = u16
            rgb_pages[s] = _d2_rgb(u16)

    order = sorted(pmap)                       # load order == ascending slot
    cells = []
    for layer, offs in _DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        if layer not in layers:
            continue
        for o in offs:
            tx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            ty = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            slot = sec9[o + T_TEX]
            fx = sec9[o + T_TEX2]
            eff = fx if (fx and fx in pmap) else slot
            pi = sec9[o + T_PAL]
            p = pmap.get(eff)
            rec = {'layer': layer, 'x': tx, 'y': ty, 'slot': slot, 'fx': fx,
                   'eff': eff, 'pal': pi, 'off': o,
                   'depth': None, 'missing': p is None,
                   'black': False, 'zero_frac': 0.0, 'mean': None,
                   'uniq': 0, 'load_pos': order.index(eff) if eff in pmap else -1}
            if p is None:
                rec['black'] = True            # never drawn at all
                cells.append(rec)
                continue
            rec['depth'] = p.depth
            grid = 8 if p.size_flag else 16
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            cx = int(round(u / UV_SCALE * grid))
            cy = int(round(v / UV_SCALE * grid))
            step = 256 // grid if p.depth == 1 else p.px // grid
            sx, sy = cx * step, cy * step
            blk_raw = raw_pages[eff][sy:sy + step, sx:sx + step]
            if blk_raw.shape[:2] != (step, step):
                cells.append(rec)
                continue
            if p.depth == 1:
                if pi >= npg_pal:
                    pi = npg_pal - 1
                rgb = pal[pi][blk_raw]
                rec['zero_frac'] = float((blk_raw == 0).mean())
            else:
                rgb = rgb_pages[eff][sy:sy + step, sx:sx + step]
                # on a truecolor page 0x0000 IS transparent -- nothing drawn
                rec['zero_frac'] = float((blk_raw == 0).mean())
            rec['uniq'] = int(len(np.unique(blk_raw)))
            rec['mean'] = float(rgb.mean())
            rec['black'] = bool((rgb.max(axis=2) <= BLACK_MAX).mean()
                                >= BLACK_FRAC)
            cells.append(rec)
    return cells, pmap, px, order


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('field')
    ap.add_argument('--vanilla')
    ap.add_argument('--layers', default='1')
    ap.add_argument('--margin-x', type=int, default=160,
                    help='|x| at or beyond this is the widened margin')
    a = ap.parse_args(argv)
    layers = tuple(int(x) for x in a.layers.split(','))

    A = lgp.Archive(a.flevel)
    e = A.index.get(a.field)
    if e is None:
        print('no such field', file=sys.stderr)
        return 1
    cells, pmap, px, order = scan(A.decompressed(e), layers)

    print(f'{a.field}: page_px={px}  slots={order}')
    for s in order:
        p = pmap[s]
        print(f'    slot {s:3d}  depth {p.depth}  px {getattr(p, "px", 256)}'
              f'  size_flag {p.size_flag}')
    print(f'  layer-1 cells: {len(cells)}')

    blk = [c for c in cells if c['black']]
    print(f'  BLACK cells: {len(blk)}')
    if not blk:
        return 0

    mar = [c for c in blk if abs(c['x']) >= a.margin_x]
    inn = [c for c in blk if abs(c['x']) < a.margin_x]
    print(f'    in margin (|x| >= {a.margin_x}): {len(mar)}'
          f'    interior: {len(inn)}')

    print('\n  -- slot histogram of BLACK cells (the section 6 test) --')
    h = collections.Counter((c['eff'], c['depth']) for c in blk)
    for (s, d), n in sorted(h.items()):
        pos = order.index(s) if s in order else -1
        tot = sum(1 for c in cells if c['eff'] == s)
        print(f'    slot {s:3d}  depth {d}  load_pos {pos:2d}/{len(order)}'
              f'   {n:5d} black of {tot:5d} cells on that slot')

    print('\n  -- depth split --')
    for d, n in sorted(collections.Counter(c['depth'] for c in blk).items(),
                       key=lambda kv: (kv[0] is None, kv[0])):
        print(f'    depth {d}: {n}')

    d2 = [c for c in blk if c['depth'] == 2]
    if d2:
        allzero = [c for c in d2 if c['zero_frac'] >= 0.99]
        print(f'\n  -- depth-2 black cells: {len(d2)}, of which '
              f'{len(allzero)} are >=99% 0x0000 (TRANSPARENT, not drawn) --')

    print('\n  -- x column histogram of BLACK cells --')
    for x, n in sorted(collections.Counter(c['x'] for c in blk).items()):
        print(f'    x={x:5d}  {n:4d}')

    print('\n  -- palette histogram of BLACK cells --')
    for pi, n in sorted(collections.Counter(c['pal'] for c in blk).items()):
        print(f'    pal {pi:3d}  {n:4d}')

    print('\n  -- first 40 black cells --')
    for c in sorted(blk, key=lambda c: (c['x'], c['y']))[:40]:
        print(f'    x={c["x"]:5d} y={c["y"]:5d} slot={c["slot"]:3d} '
              f'fx={c["fx"]:3d} eff={c["eff"]:3d} depth={c["depth"]} '
              f'pal={c["pal"]:3d} uniq={c["uniq"]:3d} '
              f'zero={c["zero_frac"]:.2f} mean={c["mean"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
