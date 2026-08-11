#!/usr/bin/env python3
"""
diag_marginart_trace.py -- replay ff7nx_marginart.fill_field's per-cell
decision for ONE field, using the real .iro art, and print which branch each
cell took.

    python3 diag_marginart_trace.py md8_1 \
        --cells -224:56,-224:72,-224:88,208:40

Reads the module's own constants and helpers, so what it prints is what the
build does. Writes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC              # noqa: E402
import ff7nx_marginart as MA          # noqa: E402
import ff7nx_marginblack as MB        # noqa: E402
import ff7nx_fieldbg                  # noqa: E402
import field_bg_repack                # noqa: E402
import iro                            # noqa: E402
import lgp                            # noqa: E402

TILE = 16
UV_SCALE = 10_000_000
T_DSTX, T_DSTY, T_PAL, T_TEX, T_TEX2 = 2, 4, 22, 32, 34
T_SRC_X_BIG = 42
SECTION9 = 8


def build_provider(px):
    with open(os.path.join(_HERE, 'settings.json')) as fh:
        settings = json.load(fh)
    path = os.path.join(_HERE, 'mods', 'CosmosLimitBreak.iro')
    import tempfile
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


def tile_srcs(sec9, tex_start, pmap, want):
    """(x, y) -> (slot, pal, sx, sy) for the layer-1 tiles asked for."""
    out = {}
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
            p = pmap.get(eff)
            if p is None:
                continue
            grid = 8 if p.size_flag else 16
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            step = 256 // grid
            out[(tx, ty)] = (eff, sec9[o + T_PAL],
                             int(round(u / UV_SCALE * grid)) * step,
                             int(round(v / UV_SCALE * grid)) * step)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('--vanilla', default='game_data_files/field/flevel.lgp')
    ap.add_argument('--cells', default='-224:56,-224:72,-224:88,208:40')
    ap.add_argument('--scope', default='all')
    ap.add_argument('--px', type=int, default=512)
    a = ap.parse_args(argv)
    want = {tuple(int(v) for v in t.split(':')) for t in a.cells.split(',')}

    px = ff7nx_fieldbg.page_px() or a.px
    print(f'page_px = {px}')
    print(f'HONOUR_MOD_ALPHA={MA.HONOUR_MOD_ALPHA} '
          f'DARKEN_MARGIN_PLACEHOLDERS={MA.DARKEN_MARGIN_PLACEHOLDERS} '
          f'MAX_QUANT_ERR={MA.MAX_QUANT_ERR} BORROW={MA.BORROW}')

    prov = build_provider(px)
    art = MA.provider_source(prov)

    A = lgp.Archive(os.path.join(_HERE, a.vanilla))
    raw = A.decompressed(A.index[a.field])
    parts = lgp.split_sections(raw)
    cols, hdr, npg, cpp = MB.palette_colours(parts[MA.SECTION_PALETTE])
    surv = DC.survey(parts[SECTION9])
    sec9 = parts[SECTION9]
    pages, tex_start, _e, _px = DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}

    cells, pgs, arrays, placeholder = MA.fillable_cells(parts, surv, a.scope)
    prgbs = [MA.palette_rgb(cols[p]) for p in range(npg)]

    srcs = tile_srcs(sec9, tex_start, pmap, want)
    if not srcs:
        print('  ! none of those cells exist as layer-1 tiles in the VANILLA '
              'archive -- they are tiles the widening ADDED, so trace against '
              'the widened source instead')

    # the widened archive is what marginart actually sees; find the tiles there
    print('\n--- fillable/placeholder membership ---')
    for (slot, pal), cs in sorted(cells.items()):
        n_ph = sum(1 for c in cs if (slot, c[0], c[1]) in placeholder)
        print(f'  slot {slot:3d} pal {pal:3d}: {len(cs):4d} cells, '
              f'{n_ph:4d} placeholders')

    print('\n--- per-cell decision replay ---')
    fn = prov.open(a.field)
    for (tx, ty), (slot, pal, sx, sy) in sorted(srcs.items()):
        print(f'\n  cell x={tx} y={ty}  slot={slot} pal={pal} src=({sx},{sy})')
        print(f'    in placeholder set: {(slot, sx, sy) in placeholder}')
        got = art(a.field, slot, pal)
        if got is None:
            print('    NO ART SHIPPED for this (page, palette) -- st.no_dds')
            continue
        img, used = got
        print(f'    art page {slot}: requested pal {pal}, '
              f'BORROWED pal {used}   img {img.shape}')
        k = img.shape[0] // 256
        src = img[sy * k:(sy + TILE) * k, sx * k:(sx + TILE) * k]
        if src.shape[:2] != (TILE * k, TILE * k):
            print('    src block out of range -- st.no_dds')
            continue
        small = (np.ascontiguousarray(src[..., :3])
                 .reshape(TILE, k, TILE, k, 3).mean(axis=(1, 3)))
        cover = (np.ascontiguousarray(src[..., 3])
                 .reshape(TILE, k, TILE, k).mean(axis=(1, 3)))
        cov = (cover >= 128) if MA.HONOUR_MOD_ALPHA else np.ones_like(cover, bool)
        print(f'    coverage: {100.0 * cov.mean():.1f}% covered '
              f'({int((~cov).sum())} of 256 texels uncovered)')
        print(f'    raw small: max {small.max():.1f} '
              f'mean {small.mean():.1f}')
        if cov.any():
            print(f'    COVERED-only: max {small[cov].max():.1f} '
                  f'mean {small[cov].mean():.1f}')
        else:
            print('    COVERED-only: nothing covered at all')
        ext = small
        if MA.HONOUR_MOD_ALPHA and not cov.all() and cov.any():
            ext = MA._extend_into_gap(small, cov)
            print(f'    after _extend_into_gap: max {ext.max():.1f} '
                  f'mean {ext.mean():.1f}')
        gate = (ext[cov].max() if cov.any() else 0)
        print(f'    GATE  small[_cov].max() = {gate:.1f}  '
              f'(<= 24 means EMPTY SOURCE)')
        if gate <= 24:
            if (slot, sx, sy) in placeholder and MA.DARKEN_MARGIN_PLACEHOLDERS:
                print('    -> DARKEN branch: writes the near-black art '
                      '(st.darkened)  ** THIS IS A BLACK SQUARE **')
            else:
                print('    -> SKIPPED as empty source (st.black); keeps '
                      'vanilla indices')
                continue
        idx = MA.quantise(ext.astype(np.uint8), prgbs[pal])
        err = float(np.abs(prgbs[pal][idx].astype(np.int16)
                           - ext.astype(np.int16))[cov].mean()) \
            if cov.any() else 0.0
        out_rgb = prgbs[pal][idx]
        print(f'    quantise err {err:.2f} (MAX {MA.MAX_QUANT_ERR}) '
              f'-> written mean {out_rgb.mean():.2f} '
              f'max {out_rgb.max()}')
        if err > MA.MAX_QUANT_ERR:
            print('    -> REFUSED as wildly off-colour (st.wild)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
