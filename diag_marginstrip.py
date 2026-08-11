#!/usr/bin/env python3
"""
diag_marginstrip.py -- draw the field from the MOD's art alone, at the source
coordinates the widened section 9 names, and mark which tiles the widening
added.

The four black cells of md8_1 turned out to be faithful reproductions of
opaque near-black source art at 100% coverage. The added margin tiles land in
the raster tail of the vanilla atlas -- space no vanilla tile referenced. This
draws that tail in its screen positions so "the tail holds real margin art"
can be told apart from "the tail holds leftovers".

    python3 diag_marginstrip.py md8_1 -o md8_1_modart.png

Writes a PNG. Touches no archive.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC              # noqa: E402
import ff7nx_marginart as MA          # noqa: E402
import field_bg_repack                # noqa: E402
import iro                            # noqa: E402
import lgp                            # noqa: E402

TILE = 16
UV_SCALE = 10_000_000
T_DSTX, T_DSTY, T_PAL, T_TEX, T_TEX2 = 2, 4, 22, 32, 34
T_SRC_X_BIG = 42
CHUNK_DIR = 'cache/CosmosLimitBreak/LIMIT BREAK/flevel.lgp'


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
    return field_bg_repack.ArtProvider([(path, allowed)], px, print)


def tiles_of(sec9, layer_want=1):
    pages, tex_start, _e, _px = DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    out = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        if layer != layer_want:
            continue
        for o in offs:
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            dy = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            slot = sec9[o + T_TEX]
            fx = sec9[o + T_TEX2]
            eff = fx if (fx and fx in pmap) else slot
            p = pmap.get(eff)
            grid = 8 if (p is not None and p.size_flag) else 16
            step = 256 // grid
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            sx = int(round(u / UV_SCALE * grid)) * step
            sy = int(round(v / UV_SCALE * grid)) * step
            out.append((dx, dy, sec9[o + T_PAL], eff, sx, sy))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--vanilla', default='game_data_files/field/flevel.lgp')
    ap.add_argument('--scale', type=int, default=2,
                    help='output texels per source pixel')
    a = ap.parse_args(argv)
    out = a.out or f'{a.field}_modart.png'

    VA = lgp.Archive(os.path.join(_HERE, a.vanilla))
    vsec9 = lgp.split_sections(VA.decompressed(VA.index[a.field]))[8]
    vkey = {(d[0], d[1]) for d in tiles_of(vsec9)}

    with open(os.path.join(_HERE, CHUNK_DIR,
                           f'{a.field}.chunk.9'), 'rb') as fh:
        wt = tiles_of(fh.read())

    xs = [d[0] for d in wt]
    ys = [d[1] for d in wt]
    x0, y0 = min(xs), min(ys)
    w = (max(xs) + TILE - x0)
    h = (max(ys) + TILE - y0)
    k = a.scale
    canvas = np.zeros((h * k, w * k, 3), np.uint8)
    mark = np.zeros((h * k, w * k), bool)

    prov = build_provider(a.px)
    art = MA.provider_source(prov)
    cache = {}
    drawn = added = 0
    for dx, dy, pal, slot, sx, sy in wt:
        got = cache.get(slot)
        if got is None:
            got = art(a.field, slot, 0)
            cache[slot] = got if got is not None else False
        if got is False or got is None:
            continue
        img, _u = got
        s = img.shape[0] // 256
        blk = img[sy * s:(sy + TILE) * s, sx * s:(sx + TILE) * s, :3]
        if blk.shape[:2] != (TILE * s, TILE * s):
            continue
        if s != k:
            blk = np.array(Image.fromarray(blk).resize(
                (TILE * k, TILE * k), Image.LANCZOS))
        py, px_ = (dy - y0) * k, (dx - x0) * k
        canvas[py:py + TILE * k, px_:px_ + TILE * k] = blk
        drawn += 1
        if (dx, dy) not in vkey:
            mark[py:py + TILE * k, px_:px_ + TILE * k] = True
            added += 1

    # red hairline around every tile the widening added
    edge = mark & ~np.pad(mark, 1)[2:, 1:-1] | mark & ~np.pad(mark, 1)[:-2, 1:-1] \
        | mark & ~np.pad(mark, 1)[1:-1, 2:] | mark & ~np.pad(mark, 1)[1:-1, :-2]
    vis = canvas.copy()
    vis[edge] = (255, 0, 0)

    Image.fromarray(vis).save(os.path.join(_HERE, out))
    Image.fromarray(canvas).save(os.path.join(_HERE, out.replace(
        '.png', '_clean.png')))
    print(f'{a.field}: {drawn} tiles drawn, {added} of them added by the '
          f'widening -> {out}  ({w}x{h} source px, x0={x0} y0={y0})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
