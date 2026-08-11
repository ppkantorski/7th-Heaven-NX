#!/usr/bin/env python3
"""
diag_blackwhy.py -- the four black cells of md8_1 are DRAWN, not dropped.
So the question is not "which pass failed to write" but "what did it write,
and what colour does the palette it names give those indices".

Prints, per black cell:
    the exact indices in the 16x16 block and their counts
    the RGB the named palette gives each index
    the RGB every OTHER palette in the field would give the same indices
    the same block in the VANILLA archive, for reference only

Nothing here changes the archive.
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

UV_SCALE = 10_000_000
T_DSTX, T_DSTY, T_PAL, T_TEX, T_TEX2 = 2, 4, 22, 32, 34
T_SRC_X_BIG = 42


def _pal_rgb(sec3):
    cols, hdr, npg, cpp = MB.palette_colours(sec3)
    v = cols.astype(np.uint32).reshape(npg, cpp)
    return np.stack([((v & 31) << 3).astype(np.uint8),
                     (((v >> 5) & 31) << 3).astype(np.uint8),
                     (((v >> 10) & 31) << 3).astype(np.uint8)], -1)


def load(path, field):
    A = lgp.Archive(path)
    raw = A.decompressed(A.index[field])
    parts = lgp.split_sections(raw)
    sec9 = parts[8]
    pages, tex_start, _e, px = _DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    arr = {}
    for s, p in pmap.items():
        if p.depth == 1:
            arr[s] = np.frombuffer(p.data, np.uint8).reshape(256, 256)
    return sec9, tex_start, pmap, arr, _pal_rgb(parts[3])


def block(sec9, o, pmap, arr):
    slot = sec9[o + T_TEX]
    fx = sec9[o + T_TEX2]
    eff = fx if (fx and fx in pmap) else slot
    p = pmap.get(eff)
    if p is None or p.depth != 1:
        return None, eff
    grid = 8 if p.size_flag else 16
    u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
    step = 256 // grid
    sx = int(round(u / UV_SCALE * grid)) * step
    sy = int(round(v / UV_SCALE * grid)) * step
    return arr[eff][sy:sy + step, sx:sx + step], eff


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('field')
    ap.add_argument('--vanilla')
    ap.add_argument('--cells', default='-224:56,-224:72,-224:88,208:40')
    a = ap.parse_args(argv)
    want = set()
    for tok in a.cells.split(','):
        x, y = tok.split(':')
        want.add((int(x), int(y)))

    sec9, tex_start, pmap, arr, pal = load(a.flevel, a.field)
    V = load(a.vanilla, a.field) if a.vanilla else None

    for layer, offs in _DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        if layer != 1:
            continue
        for o in offs:
            tx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            ty = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            if (tx, ty) not in want:
                continue
            pi = sec9[o + T_PAL]
            blk, eff = block(sec9, o, pmap, arr)
            print(f'\n=== cell x={tx} y={ty}  slot={eff}  palette={pi} ===')
            if blk is None:
                print('   depth-2 or missing page')
                continue
            cnt = collections.Counter(blk.ravel().tolist())
            print('   indices in the block (index: count) ->'
                  ' RGB under the NAMED palette')
            for idx, n in sorted(cnt.items(), key=lambda kv: -kv[1]):
                rgb = pal[pi][idx]
                print(f'     idx {idx:3d}  x{n:4d}   named pal {pi:3d}'
                      f' -> RGB {tuple(int(q) for q in rgb)}')
            print('   the SAME indices under every other palette:')
            for q in range(pal.shape[0]):
                cols = [tuple(int(z) for z in pal[q][idx]) for idx in
                        sorted(cnt)]
                mean = float(np.mean([pal[q][idx].mean() for idx in cnt
                                      for _ in range(cnt[idx])]))
                mark = ' <- NAMED' if q == pi else ''
                print(f'     pal {q:3d}  mean {mean:6.2f}  {cols}{mark}')
            if V:
                vsec9, vtex, vpmap, varr, vpal = V
                for vlayer, voffs in _DC.walk_layers(
                        vsec9, vsec9.find(b'BACK'), vtex):
                    if vlayer != 1:
                        continue
                    for vo in voffs:
                        vx = struct.unpack_from('<h', vsec9, vo + T_DSTX)[0]
                        vy = struct.unpack_from('<h', vsec9, vo + T_DSTY)[0]
                        if (vx, vy) != (tx, ty):
                            continue
                        vpi = vsec9[vo + T_PAL]
                        vblk, veff = block(vsec9, vo, vpmap, varr)
                        if vblk is None:
                            continue
                        vc = collections.Counter(vblk.ravel().tolist())
                        vmean = float(np.mean(
                            [vpal[vpi][i].mean() for i in vc
                             for _ in range(vc[i])]))
                        print(f'   VANILLA same cell: slot={veff} pal={vpi}'
                              f'  uniq={len(vc)}  mean={vmean:.2f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
