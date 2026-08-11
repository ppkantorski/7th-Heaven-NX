#!/usr/bin/env python3
"""
diag_whereart.py -- a collapsed cell holds SOMETHING. Where did that
something come from?

For each named cell, search every 16x16 block position on the mod's page for
the one whose quantised art best matches the INDICES the built archive holds.

  * best match at the cell's own (sx, sy)  -> the pass wrote this on purpose
  * best match somewhere else              -> the tile's UV or the cell's
                                              location moved without its
                                              pixels, or vice versa
  * no good match anywhere                 -> the block is not Cosmos art at
                                              all; it is vanilla, or a fill
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC              # noqa: E402
import ff7nx_marginart as MA          # noqa: E402
import ff7nx_marginblack as MB        # noqa: E402
import field_bg_repack                # noqa: E402
import iro                            # noqa: E402
import lgp                            # noqa: E402

TILE = 16
UV_SCALE = 10_000_000
T_DSTX, T_DSTY, T_PAL, T_TEX, T_TEX2 = 2, 4, 22, 32, 34
T_SRC_X_BIG = 42
SECTION9 = 8
BUILT_DEFAULT = ('sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7/'
                 'workingdir/data/field/flevel.lgp')


def build_provider(px):
    with open(os.path.join(_HERE, 'settings.json')) as fh:
        settings = json.load(fh)
    path = os.path.join(_HERE, 'mods', 'CosmosLimitBreak.iro')
    xml = iro.read_one(path, 'mod.xml')
    manifest = None
    if xml:
        with tempfile.NamedTemporaryFile('wb', suffix='.xml',
                                         delete=False) as tf:
            tf.write(xml)
            tmp = tf.name
        manifest = iro.Manifest(tmp)
        os.unlink(tmp)
    opts = settings.get('CosmosLimitBreak.iro', {}).get('options', {})
    folders = iro.active_folders(manifest, opts) if manifest else []
    allowed = {f.lower().replace('\\', '/').rstrip('/') + '/'
               for f in folders} or None
    return field_bg_repack.ArtProvider([(path, allowed)], px, lambda *_: None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('--flevel', default=BUILT_DEFAULT)
    ap.add_argument('--vanilla', default='game_data_files/field/flevel.lgp')
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--cells', default='-224:56,-224:72,-224:88,208:40,'
                                       '-224:104,208:-88')
    a = ap.parse_args(argv)
    want = {tuple(int(v) for v in t.split(':')) for t in a.cells.split(',')}

    A = lgp.Archive(os.path.join(_HERE, a.flevel))
    parts = lgp.split_sections(A.decompressed(A.index[a.field]))
    sec9 = parts[SECTION9]
    cols, hdr, npg, cpp = MB.palette_colours(parts[MA.SECTION_PALETTE])
    prgbs = [MA.palette_rgb(cols[p]) for p in range(npg)]
    pages, tex_start, _e, _px = DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    arr = {s: np.frombuffer(p.data, np.uint8).reshape(256, 256)
           for s, p in pmap.items() if p.depth == 1}

    V = lgp.Archive(os.path.join(_HERE, a.vanilla))
    vparts = lgp.split_sections(V.decompressed(V.index[a.field]))
    vsec9 = vparts[SECTION9]
    vpages, _vt, _ve, _vpx = DC.parse_pages(vsec9)
    vmap = {p.slot: p for p in vpages if p is not None}
    varr = {s: np.frombuffer(p.data, np.uint8).reshape(256, 256)
            for s, p in vmap.items() if p.depth == 1}

    prov = build_provider(a.px)
    art = MA.provider_source(prov)
    cache = {}

    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        if layer != 1:
            continue
        for o in offs:
            tx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            ty = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            if (tx, ty) not in want:
                continue
            slot = sec9[o + T_TEX]
            fx = sec9[o + T_TEX2]
            eff = fx if (fx and fx in pmap) else slot
            pi = sec9[o + T_PAL]
            p = pmap[eff]
            grid = 8 if p.size_flag else 16
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            step = 256 // grid
            sx = int(round(u / UV_SCALE * grid)) * step
            sy = int(round(v / UV_SCALE * grid)) * step
            blk = arr[eff][sy:sy + TILE, sx:sx + TILE].astype(np.int16)
            print(f'\n=== x={tx} y={ty} slot={eff} pal={pi} '
                  f'src=({sx},{sy}) ===')
            print(f'   built indices {sorted(np.unique(blk).tolist())}')

            # is it vanilla's own content, unchanged?
            if eff in varr:
                van = varr[eff][sy:sy + TILE, sx:sx + TILE].astype(np.int16)
                print(f'   identical to VANILLA at same src: '
                      f'{bool((blk == van).all())}   '
                      f'vanilla indices {sorted(np.unique(van).tolist())}')
                # anywhere in the vanilla page?
                hits = []
                for yy in range(0, 256, TILE):
                    for xx in range(0, 256, TILE):
                        if (varr[eff][yy:yy + TILE, xx:xx + TILE]
                                .astype(np.int16) == blk).all():
                            hits.append((xx, yy))
                print(f'   exact match elsewhere in the VANILLA page: {hits}')

            key = (eff, pi)
            if key not in cache:
                cache[key] = art(a.field, eff, pi)
            got = cache[key]
            if got is None:
                print('   mod ships no art for this page')
                continue
            img, used = got
            k = img.shape[0] // 256
            best = []
            for yy in range(0, 256, TILE):
                for xx in range(0, 256, TILE):
                    src = img[yy * k:(yy + TILE) * k, xx * k:(xx + TILE) * k]
                    if src.shape[:2] != (TILE * k, TILE * k):
                        continue
                    small = (np.ascontiguousarray(src[..., :3])
                             .reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3)))
                    cov = ((np.ascontiguousarray(src[..., 3])
                            .reshape(TILE, k, TILE, k).mean(axis=(1, 3)))
                           >= 128)
                    ext = small
                    if cov.any() and not cov.all():
                        ext = MA._extend_into_gap(small, cov)
                    idx = MA.quantise(ext.astype(np.uint8),
                                      prgbs[pi]).astype(np.int16)
                    d = float(np.abs(idx - blk).mean())
                    same = int((idx == blk).sum())
                    best.append((d, same, xx, yy))
            best.sort()
            own = [b for b in best if (b[2], b[3]) == (sx, sy)]
            print(f'   art match at OWN src: mean|di| '
                  f'{own[0][0]:.1f}, {own[0][1]}/256 identical'
                  if own else '   own src not scanned')
            print('   best 3 art matches anywhere on the page:')
            for d, same, xx, yy in best[:3]:
                print(f'      ({xx},{yy})  mean|di| {d:6.1f}  '
                      f'{same:3d}/256 identical')
    return 0


if __name__ == '__main__':
    sys.exit(main())
