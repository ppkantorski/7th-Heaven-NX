#!/usr/bin/env python3
"""
diag_artloss.py -- per layer-1 cell, compare what the BUILT archive draws
against what Cosmos's art would give through the same palette.

This is the "are the textures actually being leveraged" measurement. For each
cell it prints/collects:

    built_mean   the colour the console will show
    art_mean     the colour the mod's art quantises to in the SAME palette
    delta        art_mean - built_mean

A cell where the mod ships bright art and the archive holds near-black is a
LOST cell -- the black square. A cell where both agree is fine.

    python3 diag_artloss.py md8_1
    python3 diag_artloss.py md8_1 --csv artloss_md8_1.csv
"""
from __future__ import annotations

import argparse
import csv
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
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--csv')
    ap.add_argument('--min-delta', type=float, default=20.0)
    a = ap.parse_args(argv)

    A = lgp.Archive(os.path.join(_HERE, a.flevel))
    parts = lgp.split_sections(A.decompressed(A.index[a.field]))
    sec9 = parts[SECTION9]
    cols, hdr, npg, cpp = MB.palette_colours(parts[MA.SECTION_PALETTE])
    prgbs = [MA.palette_rgb(cols[p]) for p in range(npg)]
    pages, tex_start, _e, px = DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    arr = {s: np.frombuffer(p.data, np.uint8).reshape(256, 256)
           for s, p in pmap.items() if p.depth == 1}

    prov = build_provider(a.px)
    art = MA.provider_source(prov)
    art_cache = {}

    def art_for(page, pal):
        key = (page, pal)
        if key not in art_cache:
            art_cache[key] = art(a.field, page, pal)
        return art_cache[key]

    rows = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        if layer != 1:
            continue
        for o in offs:
            tx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            ty = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            slot = sec9[o + T_TEX]
            fx = sec9[o + T_TEX2]
            eff = fx if (fx and fx in pmap) else slot
            pi = sec9[o + T_PAL]
            p = pmap.get(eff)
            if p is None or p.depth != 1:
                continue                      # depth-2 handled elsewhere
            grid = 8 if p.size_flag else 16
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            step = 256 // grid
            sx = int(round(u / UV_SCALE * grid)) * step
            sy = int(round(v / UV_SCALE * grid)) * step
            blk = arr[eff][sy:sy + TILE, sx:sx + TILE]
            if blk.shape != (TILE, TILE):
                continue
            built = prgbs[pi][blk]
            got = art_for(eff, pi)
            row = {'x': tx, 'y': ty, 'slot': eff, 'pal': pi,
                   'sx': sx, 'sy': sy,
                   'built_mean': round(float(built.mean()), 2),
                   'built_uniq': int(len(np.unique(blk))),
                   'art_mean': '', 'art_uniq': '', 'cover': '',
                   'delta': ''}
            if got is not None:
                img, used = got
                k = img.shape[0] // 256
                src = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
                if src.shape[:2] == (TILE * k, TILE * k):
                    small = (np.ascontiguousarray(src[..., :3])
                             .reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3)))
                    cover = (np.ascontiguousarray(src[..., 3])
                             .reshape(TILE, k, TILE, k).mean(axis=(1, 3)))
                    cov = cover >= 128
                    ext = small
                    if cov.any() and not cov.all():
                        ext = MA._extend_into_gap(small, cov)
                    idx = MA.quantise(ext.astype(np.uint8), prgbs[pi])
                    aout = prgbs[pi][idx]
                    row['art_mean'] = round(float(aout.mean()), 2)
                    row['art_uniq'] = int(len(np.unique(idx)))
                    row['cover'] = round(float(cov.mean()), 3)
                    row['delta'] = round(row['art_mean']
                                         - row['built_mean'], 2)
            rows.append(row)

    lost = [r for r in rows
            if r['delta'] != '' and r['delta'] >= a.min_delta]
    print(f'{a.field}: {len(rows)} layer-1 depth-1 cells')
    print(f'  cells where the mod\'s art is >= {a.min_delta} brighter than '
          f'what the archive holds: {len(lost)}')
    lost.sort(key=lambda r: -r['delta'])
    print(f'\n  {"x":>6} {"y":>5} {"slot":>4} {"pal":>4} {"src":>10} '
          f'{"built":>7} {"art":>7} {"delta":>7} {"buniq":>6} {"auniq":>6} '
          f'{"cover":>6}')
    for r in lost[:40]:
        print(f'  {r["x"]:6d} {r["y"]:5d} {r["slot"]:4d} {r["pal"]:4d} '
              f'{str((r["sx"], r["sy"])):>10} '
              f'{r["built_mean"]:7.2f} {r["art_mean"]:7.2f} '
              f'{r["delta"]:7.2f} {r["built_uniq"]:6d} {r["art_uniq"]:6d} '
              f'{r["cover"]:6.2f}')

    dark = [r for r in rows if r['built_mean'] <= 8]
    print(f'\n  cells the archive draws as near-black (built_mean <= 8): '
          f'{len(dark)}')
    for r in sorted(dark, key=lambda r: (r['x'], r['y'])):
        print(f'    x={r["x"]:5d} y={r["y"]:5d} slot={r["slot"]:3d} '
              f'pal={r["pal"]:3d} src=({r["sx"]},{r["sy"]}) '
              f'built={r["built_mean"]:.2f} art={r["art_mean"]} '
              f'buniq={r["built_uniq"]} auniq={r["art_uniq"]}')

    if a.csv:
        with open(a.csv, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f'\n  wrote {a.csv}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
