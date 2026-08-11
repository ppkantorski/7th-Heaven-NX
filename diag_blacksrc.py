#!/usr/bin/env python3
"""
diag_blacksrc.py -- for the black cells, answer three questions with numbers:

  1. did marginart WRITE this block, or leave vanilla's indices in place?
     (compare the built page block against the vanilla page block, same sx/sy)
  2. what does Cosmos's own upscale hold there -- RGB and ALPHA?
  3. under the palette the tile names, what is the best colour the block
     COULD have had?

This distinguishes "the pass declined to write" from "the pass wrote black".
"""
from __future__ import annotations

import argparse
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
T_DSTX, T_DSTY, T_PAL, T_TEX, T_TEX2 = 2, 4, 22, 32, 34
T_SRC_X_BIG = 42


def _pal_rgb(sec3):
    cols, hdr, npg, cpp = MB.palette_colours(sec3)
    v = cols.astype(np.uint32).reshape(npg, cpp)
    return np.stack([((v & 31) << 3).astype(np.uint8),
                     (((v >> 5) & 31) << 3).astype(np.uint8),
                     (((v >> 10) & 31) << 3).astype(np.uint8)], -1)


def open_field(path, field):
    A = lgp.Archive(path)
    raw = A.decompressed(A.index[field])
    parts = lgp.split_sections(raw)
    sec9 = parts[8]
    pages, tex_start, _e, px = _DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    arr = {s: np.frombuffer(p.data, np.uint8).reshape(256, 256)
           for s, p in pmap.items() if p.depth == 1}
    return sec9, tex_start, pmap, arr, _pal_rgb(parts[3])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('vanilla')
    ap.add_argument('field')
    ap.add_argument('--cells', default='-224:56,-224:72,-224:88,208:40')
    a = ap.parse_args(argv)
    want = {tuple(int(v) for v in t.split(':')) for t in a.cells.split(',')}

    sec9, tex_start, pmap, arr, pal = open_field(a.flevel, a.field)
    vsec9, vtex, vpmap, varr, vpal = open_field(a.vanilla, a.field)

    for layer, offs in _DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        if layer != 1:
            continue
        for o in offs:
            tx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            ty = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            if (tx, ty) not in want:
                continue
            pi = sec9[o + T_PAL]
            slot = sec9[o + T_TEX]
            fx = sec9[o + T_TEX2]
            eff = fx if (fx and fx in pmap) else slot
            p = pmap[eff]
            grid = 8 if p.size_flag else 16
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            step = 256 // grid
            sx = int(round(u / UV_SCALE * grid)) * step
            sy = int(round(v / UV_SCALE * grid)) * step

            cur = arr[eff][sy:sy + TILE, sx:sx + TILE]
            print(f'\n=== x={tx} y={ty}  slot={eff} pal={pi}'
                  f'  src=({sx},{sy}) ===')
            if eff in varr:
                van = varr[eff][sy:sy + TILE, sx:sx + TILE]
                same = bool((cur == van).all())
                print(f'   built block == vanilla block ?  {same}'
                      f'   ({int((cur != van).sum())} of 256 pixels differ)')
                vrgb = vpal[pi][van]
                print(f'   vanilla indices uniq={len(np.unique(van))}'
                      f'  mean under pal {pi} = {vrgb.mean():.2f}')
            else:
                print('   slot not present in vanilla')
            crgb = pal[pi][cur]
            print(f'   built   indices uniq={len(np.unique(cur))}'
                  f'  mean under pal {pi} = {crgb.mean():.2f}')
            print(f'   index histogram: '
                  f'{sorted(np.unique(cur).tolist())}')
            # what is the brightest this palette could do for this block
            print(f'   palette {pi} overall: min {pal[pi].min()} '
                  f'max {pal[pi].max()} mean {pal[pi].mean():.1f} '
                  f'non-black entries {int((pal[pi].max(axis=1) > 8).sum())}'
                  f'/{pal[pi].shape[0]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
