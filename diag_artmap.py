#!/usr/bin/env python3
"""
diag_artmap.py -- brightness map of the MOD's own art, per 16x16 source cell,
for every page it ships for a field.

The four black cells of md8_1 are faithful reproductions of near-black source
art (raw mean 3.2-3.7, coverage 100%, quant err ~3). The question this answers
is whether that darkness is STRUCTURAL -- a padded band or a dead region of
the mod's atlas -- or scattered.

    python3 diag_artmap.py md8_1
    python3 diag_artmap.py md8_1 --pal 0 --dark 8

Prints, per page, a 16x16 grid of mean brightness per source cell, plus the
row/column profile. Writes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_marginart as MA          # noqa: E402
import field_bg_repack                # noqa: E402
import iro                            # noqa: E402

TILE = 16


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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('--pal', type=int, default=0)
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--dark', type=float, default=8.0,
                    help='mean brightness at or under this counts as dark')
    a = ap.parse_args(argv)

    prov = build_provider(a.px)
    art = MA.provider_source(prov)
    pages = sorted({k[1] for k in prov.slots
                    if k[0] == a.field.lower()})
    print(f'{a.field}: mod ships pages {pages}')

    for page in pages:
        got = art(a.field, page, a.pal)
        if got is None:
            print(f'\n=== page {page}: NO ART ===')
            continue
        img, used = got
        k = img.shape[0] // 256
        rgb = np.ascontiguousarray(img[..., :3]).astype(np.float32)
        alpha = (img[..., 3].astype(np.float32) if img.shape[2] > 3
                 else np.full(img.shape[:2], 255.0, np.float32))
        # mean brightness per 16x16 source cell -> 16x16 grid
        n = 256 // TILE
        cell = rgb.reshape(n, TILE * k, n, TILE * k, 3).mean(axis=(1, 3, 4))
        cov = (alpha.reshape(n, TILE * k, n, TILE * k) >= 8).mean(axis=(1, 3))

        print(f'\n=== page {page} (art {img.shape}, borrowed pal {used}) ===')
        print('    mean brightness per source cell, sx across, sy down')
        print('        ' + ' '.join(f'{c * TILE:>4d}' for c in range(n)))
        for r in range(n):
            row = ' '.join(f'{v:>4.0f}' for v in cell[r])
            print(f'  {r * TILE:>4d}  {row}')

        dark = cell <= a.dark
        print(f'    dark cells (<= {a.dark:g}): {int(dark.sum())} of {n * n}')
        rowdark = dark.sum(1)
        coldark = dark.sum(0)
        print('    per-row dark count : '
              + ' '.join(f'{r * TILE}:{int(v)}' for r, v in
                         enumerate(rowdark) if v))
        print('    per-col dark count : '
              + ' '.join(f'{c * TILE}:{int(v)}' for c, v in
                         enumerate(coldark) if v))
        print(f'    mean coverage {cov.mean() * 100:.1f}%  '
              f'fully-covered cells {int((cov >= 0.999).sum())}/{n * n}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
