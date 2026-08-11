#!/usr/bin/env python3
"""
diag_srcref.py -- who references which source cell of which page, in VANILLA
and in the mod's WIDENED section 9, and how bright the mod's art is there.

The four black cells of md8_1 are faithful reproductions of opaque near-black
source art. So the question is no longer "which pass broke it" but "why do the
widened margin tiles point at a dark region of the atlas".

This prints, per page, for every 16x16 source cell:
    V   referenced by a vanilla layer-1 tile
    W   referenced by a widened (mod chunk.9) layer-1 tile
    .   referenced by neither -- dead atlas space

next to the mod's art brightness, so "the margin points at dead space" can be
separated from "the mod authored it dark on purpose".

    python3 diag_srcref.py md8_1
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
import field_bg_repack                # noqa: E402
import iro                            # noqa: E402
import lgp                            # noqa: E402

TILE = 16
T_DSTX, T_DSTY, T_PAL, T_TEX, T_TEX2 = 2, 4, 22, 32, 34
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


UV_SCALE = 10_000_000
T_SRC_X_BIG = 42


def tiles_of(sec9, layer_want=1):
    """(dstx, dsty, pal, slot, fx, sx, sy) for one section 9.

    Source coords come from the big UV pair at +42/+46, scaled by the page's
    grid -- the byte pair at +0/+1 is the small UV and is not what the 512px
    path uses.
    """
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
            out.append((dx, dy, sec9[o + T_PAL], eff, fx, sx, sy))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--dark', type=float, default=8.0)
    ap.add_argument('--vanilla', default='game_data_files/field/flevel.lgp')
    a = ap.parse_args(argv)

    VA = lgp.Archive(os.path.join(_HERE, a.vanilla))
    vsec9 = lgp.split_sections(VA.decompressed(VA.index[a.field]))[8]
    vt = tiles_of(vsec9)

    chunk = os.path.join(_HERE, CHUNK_DIR, f'{a.field}.chunk.9')
    with open(chunk, 'rb') as fh:
        wsec9 = fh.read()
    wt = tiles_of(wsec9)

    vkey = {(d[0], d[1]) for d in vt}
    print(f'{a.field}: vanilla layer-1 tiles {len(vt)}, '
          f'widened {len(wt)}, added {len(wt) - len(vt)}')

    prov = build_provider(a.px)
    art = MA.provider_source(prov)

    n = 256 // TILE
    pages = sorted({t[3] for t in wt} | {t[3] for t in vt})
    for page in pages:
        vref = np.zeros((n, n), bool)
        wref = np.zeros((n, n), bool)
        newref = np.zeros((n, n), bool)
        for d in vt:
            if d[3] == page:
                vref[d[6] // TILE, d[5] // TILE] = True
        for d in wt:
            if d[3] == page:
                wref[d[6] // TILE, d[5] // TILE] = True
                if (d[0], d[1]) not in vkey:
                    newref[d[6] // TILE, d[5] // TILE] = True

        got = art(a.field, page, 0)
        if got is None:
            cell = np.full((n, n), np.nan)
            covm = np.zeros((n, n))
        else:
            img, _u = got
            k = img.shape[0] // 256
            rgb = np.ascontiguousarray(img[..., :3]).astype(np.float32)
            cell = rgb.reshape(n, TILE * k, n, TILE * k,
                               3).mean(axis=(1, 3, 4))
            al = (img[..., 3].astype(np.float32) if img.shape[2] > 3
                  else np.full(img.shape[:2], 255.0, np.float32))
            covm = (al.reshape(n, TILE * k, n,
                               TILE * k) >= 8).mean(axis=(1, 3))

        print(f'\n=== page {page} ===')
        print('    V vanilla-referenced  N added by the widening  '
              'W widened-only  . unreferenced')
        print('        ' + ' '.join(f'{c * TILE:>5d}' for c in range(n)))
        for r in range(n):
            bits = []
            for c in range(n):
                if newref[r, c]:
                    m = 'N'
                elif vref[r, c]:
                    m = 'V'
                elif wref[r, c]:
                    m = 'W'
                else:
                    m = '.'
                b = cell[r, c]
                bits.append(f'{m}{b:>4.0f}' if b == b else f'{m}   -')
            print(f'  {r * TILE:>4d}  ' + ' '.join(bits))

        newdark = int((newref & (cell <= a.dark)).sum())
        newtot = int(newref.sum())
        vdark = int((vref & (cell <= a.dark)).sum())
        vtot = int(vref.sum())
        deadtot = int((~vref & ~wref).sum())
        deaddark = int(((~vref & ~wref) & (cell <= a.dark)).sum())
        if newtot or vtot:
            print(f'    ADDED cells   : {newdark}/{newtot} dark'
                  + (f'  ({100 * newdark / newtot:.0f}%)' if newtot else ''))
            print(f'    vanilla cells : {vdark}/{vtot} dark'
                  + (f'  ({100 * vdark / vtot:.0f}%)' if vtot else ''))
            print(f'    unreferenced  : {deaddark}/{deadtot} dark'
                  + (f'  ({100 * deaddark / deadtot:.0f}%)'
                     if deadtot else ''))
            print(f'    mean coverage {covm.mean() * 100:.1f}%')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
