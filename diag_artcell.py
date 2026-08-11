#!/usr/bin/env python3
"""
diag_artcell.py -- what does Cosmos actually ship at one (page, sx, sy), and
what would ff7nx_marginart do with it?

    python3 diag_artcell.py md8_1 1:192:208 1:160:240 1:144:240 2:48:16 \
        --pal 3

Takes page/source coordinates straight from the BUILT archive (which keeps
the mod's page numbering, because marginart runs before the repack renumbers
anything) and asks the real ArtProvider for that block. Writes nothing.
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
import ff7nx_marginblack as MB        # noqa: E402
import field_bg_repack                # noqa: E402
import iro                            # noqa: E402
import lgp                            # noqa: E402

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
    ap.add_argument('cells', nargs='+', help='page:sx:sy')
    ap.add_argument('--pal', type=int, default=3)
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--flevel',
                    default='sdout/atmosphere/contents/0100A5B00BDC6000/'
                            'romfs/ff7/workingdir/data/field/flevel.lgp')
    a = ap.parse_args(argv)

    A = lgp.Archive(os.path.join(_HERE, a.flevel))
    parts = lgp.split_sections(A.decompressed(A.index[a.field]))
    cols, hdr, npg, cpp = MB.palette_colours(parts[MA.SECTION_PALETTE])
    prgbs = [MA.palette_rgb(cols[p]) for p in range(npg)]

    prov = build_provider(a.px)
    art = MA.provider_source(prov)
    print(f'HONOUR_MOD_ALPHA={MA.HONOUR_MOD_ALPHA} '
          f'DARKEN={MA.DARKEN_MARGIN_PLACEHOLDERS} '
          f'MAX_QUANT_ERR={MA.MAX_QUANT_ERR}')
    print(f'pages the mod ships for {a.field}: '
          f'{sorted({k[1] for k in prov.slots if k[0] == a.field.lower()})}')

    for spec in a.cells:
        page, sx, sy = (int(v) for v in spec.split(':'))
        print(f'\n=== page {page} src ({sx},{sy})  palette {a.pal} ===')
        got = art(a.field, page, a.pal)
        if got is None:
            print('   NO ART for this page at any palette')
            continue
        img, used = got
        print(f'   art {img.shape}, borrowed palette {used}')
        k = img.shape[0] // 256
        src = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
        if src.shape[:2] != (TILE * k, TILE * k):
            print('   out of range')
            continue
        small = (np.ascontiguousarray(src[..., :3])
                 .reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3)))
        cover = (np.ascontiguousarray(src[..., 3])
                 .reshape(TILE, k, TILE, k).mean(axis=(1, 3)))
        cov = cover >= 128
        print(f'   coverage {100.0 * cov.mean():5.1f}%  '
              f'({int((~cov).sum())}/256 texels uncovered)')
        print(f'   raw      max {small.max():6.1f}  mean {small.mean():6.2f}')
        if cov.any():
            print(f'   covered  max {small[cov].max():6.1f}  '
                  f'mean {small[cov].mean():6.2f}')
        ext = small
        if not cov.all() and cov.any():
            ext = MA._extend_into_gap(small, cov)
            print(f'   extended max {ext.max():6.1f}  mean {ext.mean():6.2f}')
        gate = float(ext[cov].max()) if cov.any() else 0.0
        print(f'   GATE ext[cov].max() = {gate:.1f}   '
              f'{"EMPTY-SOURCE branch" if gate <= 24 else "normal quantise"}')
        idx = MA.quantise(ext.astype(np.uint8), prgbs[a.pal])
        out = prgbs[a.pal][idx]
        err = float(np.abs(out.astype(np.int16)
                           - ext.astype(np.int16))[cov].mean()) \
            if cov.any() else 0.0
        print(f'   would write: mean {out.mean():6.2f} max {out.max():3d} '
              f'uniq {len(np.unique(idx))}  err {err:.2f}')
        # what the SAME art would look like in the best palette available
        best = None
        for q in range(len(prgbs)):
            i2 = MA.quantise(ext.astype(np.uint8), prgbs[q])
            o2 = prgbs[q][i2]
            e2 = float(np.abs(o2.astype(np.int16)
                              - ext.astype(np.int16))[cov].mean()) \
                if cov.any() else 0.0
            if best is None or e2 < best[1]:
                best = (q, e2, o2.mean(), len(np.unique(i2)))
        print(f'   best palette for this art: {best[0]} '
              f'err {best[1]:.2f} mean {best[2]:.2f} uniq {best[3]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
