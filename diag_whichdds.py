#!/usr/bin/env python3
"""
diag_whichdds.py -- Cosmos ships one DDS per (field, page, PALETTE). For a
cell that came out black, which of those DDS files does the archive's block
actually correspond to, at the cell's OWN source coordinates?

  * matches the DDS for the palette the tile names       -> right art
  * matches a DIFFERENT palette's DDS                    -> wrong borrow
  * matches none                                         -> not mod art

Also prints, per available DDS, how bright that block is, so "the mod simply
authored it dark" can be separated from "we picked the dark one".
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


def unpack(art):
    v = np.frombuffer(art.buf, '<u2').reshape(art.px, art.px)
    r = ((v >> 11) & 0x1F).astype(np.uint16)
    g = ((v >> 5) & 0x3F).astype(np.uint16)
    b = (v & 0x1F).astype(np.uint16)
    rgb = np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4),
                    (b << 3) | (b >> 2)], -1).astype(np.uint8)
    cov = np.where(np.asarray(art.tmask).reshape(art.px, art.px),
                   np.uint8(0), np.uint8(255))
    return np.concatenate([rgb, cov[..., None]], -1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('--flevel', default=BUILT_DEFAULT)
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--cells', default='-224:56,-224:72,-224:88,208:40')
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

    prov = build_provider(a.px)
    fn = prov.open(a.field)

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
            avail = sorted(prov.palettes(eff))
            print(f'\n=== x={tx} y={ty}  page {eff}  tile names palette {pi}'
                  f'  src=({sx},{sy}) ===')
            print(f'   mod ships this page for palettes: {avail}')
            print(f'   built indices {sorted(np.unique(blk).tolist())}'
                  f'   rendered mean {prgbs[pi][blk].mean():.2f}')
            for q in avail:
                art = fn(eff, q)
                if art is None:
                    continue
                img = unpack(art)
                k = img.shape[0] // 256
                src = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
                if src.shape[:2] != (TILE * k, TILE * k):
                    continue
                small = (np.ascontiguousarray(src[..., :3])
                         .reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3)))
                cov = ((np.ascontiguousarray(src[..., 3])
                        .reshape(TILE, k, TILE, k).mean(axis=(1, 3))) >= 128)
                ext = small
                if cov.any() and not cov.all():
                    ext = MA._extend_into_gap(small, cov)
                idx = MA.quantise(ext.astype(np.uint8),
                                  prgbs[pi]).astype(np.int16)
                same = int((idx == blk).sum())
                out = prgbs[pi][idx]
                mark = '  <- the BORROW the build would pick' \
                    if q == min(avail) else ''
                print(f'     DDS pal {q:3d}: source mean {small.mean():6.2f} '
                      f'cover {100 * cov.mean():5.1f}%  -> would render '
                      f'{out.mean():6.2f}   matches built {same:3d}/256{mark}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
