#!/usr/bin/env python3
"""
diag_pipeline.py -- run the field-background passes on ONE field, in the
build's order, and print what each named cell looks like after each pass.

    python3 diag_pipeline.py md8_1

Input state is reproduced the way build.py does it: the vanilla field with
section 9 replaced by the mod's `<field>.chunk.9` (the widened section, from
the extracted Cosmos cache). Then, in order:

    ff7nx_marginart   (which internally runs ff7nx_marginpal.choose)
    ff7nx_marginpage
    ff7nx_palkey

HANDOFF-121 section 3.6: "one variable per build, and a mechanical diff of
every counter before any claim is made". This is the offline version of that
-- attribution per pass, with no build and no hardware.
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
import ff7nx_marginpage as MPG        # noqa: E402
import ff7nx_palkey as PK             # noqa: E402
import field_bg_repack                # noqa: E402
import iro                            # noqa: E402
import lgp                            # noqa: E402

TILE = 16
UV_SCALE = 10_000_000
T_DSTX, T_DSTY, T_PAL, T_TEX, T_TEX2 = 2, 4, 22, 32, 34
T_SRC_X_BIG = 42
SECTION9 = 8
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
    return field_bg_repack.ArtProvider([(path, allowed)], px, lambda *_: None)


def snapshot(raw, want):
    """(x, y) -> dict describing how that cell renders right now."""
    parts = lgp.split_sections(raw)
    sec9 = parts[SECTION9]
    cols, hdr, npg, cpp = MB.palette_colours(parts[3])
    prgbs = [MA.palette_rgb(cols[q]) for q in range(npg)]
    pages, tex_start, _e, _px = DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    arr = {s: np.frombuffer(p.data, np.uint8).reshape(256, 256)
           for s, p in pmap.items() if p.depth == 1}
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
            pi = sec9[o + T_PAL]
            p = pmap.get(eff)
            if p is None or p.depth != 1:
                out[(tx, ty)] = {'slot': eff, 'pal': pi, 'note': 'depth2/none'}
                continue
            grid = 8 if p.size_flag else 16
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            step = 256 // grid
            sx = int(round(u / UV_SCALE * grid)) * step
            sy = int(round(v / UV_SCALE * grid)) * step
            blk = arr[eff][sy:sy + TILE, sx:sx + TILE]
            out[(tx, ty)] = {
                'slot': eff, 'pal': pi, 'sx': sx, 'sy': sy,
                'mean': round(float(prgbs[pi][blk].mean()), 2),
                'uniq': int(len(np.unique(blk))),
                'zero': round(float((blk == 0).mean()), 2),
                'idx': sorted(np.unique(blk).tolist())[:6],
                'pal0': tuple(int(q) for q in prgbs[pi][0]),
            }
    return out


def show(tag, snap, want):
    print(f'\n--- after {tag} ---')
    print(f'  {"cell":>12} {"slot":>5} {"pal":>4} {"src":>11} {"mean":>8} '
          f'{"uniq":>5} {"idx0%":>6}  indices')
    for c in sorted(want):
        s = snap.get(c)
        if s is None:
            print(f'  {str(c):>12}  (no tile)')
            continue
        if 'mean' not in s:
            print(f'  {str(c):>12} {s["slot"]:5d} {s["pal"]:4d}   {s["note"]}')
            continue
        print(f'  {str(c):>12} {s["slot"]:5d} {s["pal"]:4d} '
              f'{str((s["sx"], s["sy"])):>11} {s["mean"]:8.2f} '
              f'{s["uniq"]:5d} {s["zero"]:6.2f}  {s["idx"]}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('--vanilla', default='game_data_files/field/flevel.lgp')
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--scope', default='all')
    ap.add_argument('--cells', default='-224:56,-224:72,-224:88,208:40,'
                                       '-224:104,-208:-24,-224:-56')
    a = ap.parse_args(argv)
    want = {tuple(int(v) for v in t.split(':')) for t in a.cells.split(',')}

    A = lgp.Archive(os.path.join(_HERE, a.vanilla))
    parts = lgp.split_sections(A.decompressed(A.index[a.field]))
    chunk = os.path.join(_HERE, CHUNK_DIR, f'{a.field}.chunk.9')
    with open(chunk, 'rb') as fh:
        parts[SECTION9] = fh.read()
    raw = lgp.join_sections(parts)
    print(f'{a.field}: rebuilt the pre-marginart state from {chunk}')
    print(f'  HONOUR_MOD_ALPHA={MA.HONOUR_MOD_ALPHA} '
          f'DARKEN={MA.DARKEN_MARGIN_PLACEHOLDERS} '
          f'MAX_QUANT_ERR={MA.MAX_QUANT_ERR} '
          f'KEEP_BLACK_SILHOUETTE={MA.KEEP_BLACK_SILHOUETTE}')

    show('the mod\'s own section 9 (pre-marginart)', snapshot(raw, want), want)

    prov = build_provider(a.px)
    art = MA.provider_source(prov)
    new, st = MA.fill_field(a.field, raw, lgp, art, scope=a.scope)
    print(f'\n  marginart stats: '
          + ', '.join(f'{k}={v}' for k, v in sorted(st.items())
                      if isinstance(v, int)))
    if new is not None:
        raw = new
    show('ff7nx_marginart', snapshot(raw, want), want)

    try:
        parts = lgp.split_sections(raw)
        sec9b, mst = MPG.split_section9(parts[SECTION9])
        if sec9b is not None:
            parts[SECTION9] = sec9b
            raw = lgp.join_sections(parts)
        print(f'\n  marginpage stats: {mst}')
        show('ff7nx_marginpage', snapshot(raw, want), want)
    except Exception as exc:                                    # noqa: BLE001
        print(f'\n  marginpage: {type(exc).__name__} {exc}')

    try:
        parts = lgp.split_sections(raw)
        sec3b, pst = PK.blacken_keys(parts[3], parts[SECTION9])
        if sec3b is not None:
            parts[3] = sec3b
            raw = lgp.join_sections(parts)
        print(f'\n  palkey stats: {pst}')
        show('ff7nx_palkey', snapshot(raw, want), want)
    except Exception as exc:                                    # noqa: BLE001
        print(f'\n  palkey: {type(exc).__name__} {exc}')

    out = os.path.join(_HERE, f'_pipeline_{a.field}.bin')
    with open(out, 'wb') as fh:
        fh.write(raw)
    print(f'\n  wrote {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
